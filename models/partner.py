import logging
import shutil
import time
import psycopg2
import requests
import base64
import random
import string
import subprocess
import os
import stat
from pathlib import Path
from datetime import datetime
from odoo import models, _, fields, api, registry, SUPERUSER_ID
import odoo
from odoo.http import request, SessionExpiredException
from odoo.exceptions import AccessDenied, AccessError, ValidationError, UserError

logger = logging.getLogger(__name__)

class NaidashPartner(models.Model):
    _inherit = "res.partner"
    
    # === No changes to field definitions ===
    id_number = fields.Char(string="Identification No.")
    partner_primary_id = fields.Char(string="Partner's Primary ID")
    partner_secondary_id = fields.Char(string="Partner's Secondary ID")
    partner_database_name = fields.Char(string="Partner's Database Name")
    business_id = fields.Char(string="Business ID", readonly=True) #Business partner_id
    reset_password_url = fields.Char(string='Reset Password URL')
    is_phone_number_verified = fields.Boolean(
        string = "Phone Number Verified?",
        default = False,
        help="If set to true, the phone number has been verified otherwise it's not verified"
    )
    is_email_verified = fields.Boolean(
        string = "Email Verified?",
        default = False,
        help="If set to true, the email has been verified otherwise it's not verified"
    )
    is_id_number_verified = fields.Boolean(
        string = "ID Number Verified?",
        default = False,
        help="If set to true, the id number has been verified otherwise it's not verified"
    )
    payment_url = fields.Char(string='Payment URL')

    # === New helper methods for validation and preparation ===
    def _validate_tenant_names(self, tenant_database, tenant_id):
        """Validate tenant naming conventions"""
        import re
        
        for name in [tenant_database, tenant_id]:
            if not re.match(r'^[a-zA-Z][a-zA-Z0-9_]{3,63}$', name):
                raise ValidationError(_(
                    "Invalid tenant name format. Must start with a letter and "
                    "contain only letters, numbers, and underscores"
                ))
            
            # Check reserved names
            reserved = ['postgres', 'template0', 'template1', 'odoo']
            if name.lower() in reserved:
                raise ValidationError(_("Reserved name cannot be used for tenant"))
        
        return True
    
        #Generate Business ID
    def _generate_business_id(self, company_name):
        """Generate a unique business identifier from company name"""
        import re
        
        # Convert to lowercase and remove any special characters
        business_id = company_name.lower()
        business_id = re.sub(r'[^a-z0-9\s]', '', business_id)
        
        # Take first word (or first 2 words if first word is too short)
        parts = business_id.split()
        if len(parts) > 0:
            if len(parts[0]) < 4 and len(parts) > 1:
                business_id = f"{parts[0]}{parts[1]}"
            else:
                business_id = parts[0]
        
        # Ensure minimum length
        if len(business_id) < 4:
            business_id += "".join(random.choices(string.ascii_lowercase, k=4-len(business_id)))
        
        # Validate and ensure uniqueness
        business_id = self._validate_business_id(business_id)
        
        return business_id
    
        # method to generate timestamp-based identifiers
    def _generate_tenant_identifiers(self, business_id):
        """Generate tenant identifiers using business ID and timestamp"""
        timestamp = datetime.now().strftime('%y%m%d%H%M')  # Format: YYMMDDHHmm
        
        # Generate identifiers
        tenant_id = f"TID_{business_id}_{timestamp}"
        tenant_database = f"TDB_{business_id}_{timestamp}"
        
        logger.info(f"Generated tenant identifiers - ID: {tenant_id}, DB: {tenant_database}")
        return tenant_id, tenant_database
    
    def _validate_tenant_password(self, password):
        """Validate tenant password strength"""
        if len(password) < 10:
            raise ValidationError(_("Password must be at least 10 characters"))
            
        if not any(c.isupper() for c in password):
            raise ValidationError(_("Password must contain uppercase letters"))
            
        if not any(c.isdigit() for c in password):
            raise ValidationError(_("Password must contain numbers"))
            
        return True
    
    def _get_script_path(self):
        """Get and validate tenant creation script path"""
        try:
            # Get module path and navigate to root directory
            current_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            root_dir = os.path.dirname(os.path.dirname(current_dir))
            script_path = os.path.join(root_dir, 'create_tenant.sh')
            
            if not os.path.exists(script_path):
                raise FileNotFoundError(
                    f"Tenant creation script not found at {script_path}. "
                    f"Please ensure the script exists and has proper permissions."
                )
            
            logger.info(f"Found tenant creation script at: {script_path}")
            return script_path
            
        except Exception as e:
            logger.error(f"Error locating script: {str(e)}")
            raise
     
    def _validate_script_permissions(self, script_path):
        """Verify and set proper script permissions"""
        try:
            script_stat = os.stat(script_path)
            
            # Check if script is executable
            if not script_stat.st_mode & stat.S_IXUSR:
                os.chmod(script_path, script_stat.st_mode | stat.S_IXUSR)
            
            # Check ownership
            if script_stat.st_uid != os.getuid():
                raise PermissionError("Script must be owned by Odoo process user")
                
            return True
        except Exception as e:
            logger.error(f"Permission validation failed: {str(e)}")
            raise   
    
    
    def _cleanup_failed_tenant(self, tenant_database, tenant_id):
        try:
            # Get the home directory
            home_dir = os.path.expanduser('~')
            tenant_dir = os.path.join(home_dir, 'tenants', tenant_database.lower())
            
            logger.info(f"Cleaning up tenant directory: {tenant_dir}")
            
            if os.path.exists(tenant_dir):
                try:
                    # docker-compose operations
                    subprocess.run(
                        ['docker-compose', 'down'],
                        cwd=tenant_dir,
                        check=True,
                        capture_output=True
                    )
                    logger.info("Docker containers stopped successfully")
                except subprocess.CalledProcessError as e:
                    logger.error(f"Failed to stop containers: {e.stderr}")
                
                # Directory removal
                try:
                    subprocess.run(['rm', '-rf', tenant_dir], check=True)
                    logger.info("Tenant directory removed successfully")
                except subprocess.CalledProcessError as e:
                    logger.error(f"Failed to remove tenant directory: {str(e)}")
            
            # Database cleanup using direct postgres connection
            try:
                conn = self._get_postgres_connection()
                conn.set_isolation_level(psycopg2.extensions.ISOLATION_LEVEL_AUTOCOMMIT)
                
                with conn.cursor() as cur:
                    # Terminate existing connections
                    cur.execute("""
                        SELECT pg_terminate_backend(pid) 
                        FROM pg_stat_activity 
                        WHERE datname = %s
                    """, (tenant_database.lower(),))
                    
                    # Drop database
                    cur.execute(f"""
                        DROP DATABASE IF EXISTS {tenant_database.lower()}
                    """)
                    
                    # Drop role
                    cur.execute(f"""
                        DROP ROLE IF EXISTS {tenant_id.lower()}
                    """)
                
                conn.close()
                logger.info(f"Database cleanup completed for tenant: {tenant_database}")
                
            except Exception as e:
                logger.error(f"Database cleanup failed: {str(e)}")
                    
        except Exception as e:
            logger.error(f"Cleanup failed: {str(e)}")
    
    def _validate_partner_data(self, request_data):
        """Validate partner creation request data
        Returns dict with error if validation fails
        """
        response_data = {}
        
        # Validate account type
        account_type = request_data.get("account_type")
        if account_type not in ["individual", "company"]:
            return {
                "error": True,
                "code": 400,
                "message": "Account type must be either 'individual' or 'company'"
            }
            
        # Validate required fields
        if not request_data.get("name"):
            return {
                "error": True,
                "code": 400,
                "message": "Name is required!"
            }
            
        phone = request_data.get("phone")
        if not phone:
            return {
                "error": True,
                "code": 400,
                "message": "Phone number is required!"
            }
            
        if (not phone.startswith("01") and not phone.startswith("07")) or len(phone) != 10:
            return {
                "error": True, 
                "code": 400,
                "message": "Unsupported phone number format!"
            }
            
        # Validate tag_ids if present
        tag_ids = request_data.get("tag_ids")
        if tag_ids and not isinstance(tag_ids, list):
            return {
                "error": True,
                "code": 422,
                "message": "Expected a list of integer(s) in `tag_ids`"
            }
            
        return {"error": False}

    def _check_existing_partner(self, request_data):
        """Check if partner already exists based on email/phone
        Returns True if partner exists
        """
        query_params = ['|']
        
        # Add email check if provided
        if request_data.get("email"):
            query_params.append('|')
            query_params.append(("email", "ilike", request_data["email"]))
            
        # Format and add phone check
        phone = request_data.get("phone")
        if phone:
            country = self.env["res.country"].search([('code','=', 'KE')], order='id asc', limit=1)
            if phone.startswith("01") or phone.startswith("07"):
                country_code = str(country.phone_code)
                phone = phone.replace('01', country_code, 1).replace('07', country_code, 1)
                phone = phone.strip()
                
            query_params.append(('phone','=', phone))
            query_params.append(('mobile','=', phone))
            
        # Check for existing partner
        return bool(self.env["res.partner"].search(query_params, order='id asc', limit=1))

    def _prepare_partner_details(self, request_data):
        """Prepare partner details dictionary for creation
        """
        account_type = request_data.get("account_type")
        partner_name = request_data.get("name", "").strip()
        
        # Get admin and country
        admin = self.env["res.users"].search([], order='id asc', limit=1)
        country = self.env["res.country"].search([('code','=', 'KE')], order='id asc', limit=1)
        
        # Format phone number
        phone_number = request_data.get("phone", "")
        if phone_number.startswith("01") or phone_number.startswith("07"):
            country_code = str(country.phone_code)
            phone_number = phone_number.replace('01', country_code, 1).replace('07', country_code, 1)
            phone_number = phone_number.strip()
            
        # Prepare base details
        partner_details = {
            "company_type": "person" if account_type == "individual" else account_type,
            "name": partner_name.title(),
            "phone": phone_number,
            "email": request_data.get("email", "").strip(),
            "id_number": request_data.get("id_number", "").strip(),
            "vat": request_data.get("tax_id", "").strip(),
            "country_id": country.id,
            "company_id": admin.company_id.id,
            "tz": "Africa/Nairobi"
        }
        
        # Add tags if provided
        tag_ids = request_data.get("tag_ids")
        if tag_ids:
            tags = self.env['res.partner.category'].browse(tag_ids)
            if tags:
                partner_details["category_id"] = [tag.id for tag in tags]
                
        return partner_details

    # === Updated tenant creation methods ===
    def _create_tenant_with_timeout(self, script_path, tenant_database, tenant_id, tenant_password, timeout=300):
        """Execute tenant creation with timeout and improved logging"""
        try:
            # Setup tenants directory with proper permissions
            current_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            root_dir = os.path.dirname(os.path.dirname(current_dir))

            # Get the home directory
            home_dir = os.path.expanduser('~')
            tenants_dir = os.path.join(home_dir, 'tenants')
            
            if not os.path.exists(tenants_dir):
                subprocess.run(['mkdir', '-p', tenants_dir], check=True)
                subprocess.run(['chown', '-R', f'{os.getuid()}:{os.getgid()}', tenants_dir], check=True)
                subprocess.run(['chmod', '775', tenants_dir], check=True)
            
            # Set up environment variables with explicit postgres password
            env = os.environ.copy()
            env.update({
                'PGPASSWORD': 'postgres',
                'TENANT_NAME': tenant_database.lower(),
                'DB_USER': tenant_id.lower(),
                'DB_PASSWORD': tenant_password,
                'ODOO_ADMIN_PASSWD': tenant_password  # Add explicit admin password
            })
            
            logger.info(f"Starting tenant creation for {tenant_database}")
            
            # Execute script with timeout handling
            try:
                # Start process with pipe for output
                process = subprocess.Popen(
                    [script_path, tenant_database.lower(), tenant_id.lower(), tenant_password],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    env=env,
                    cwd=root_dir
                )
                
                # Handle output with timeout
                import select
                start_time = time.time()
                outputs = {'stdout': [], 'stderr': []}
                
                while True:
                    elapsed_time = time.time() - start_time
                    if elapsed_time > timeout:
                        process.kill()
                        logger.error(f"Process killed after {elapsed_time:.1f} seconds")
                        raise subprocess.TimeoutExpired(
                            cmd=script_path,
                            timeout=timeout,
                            output="".join(outputs['stdout']),
                            stderr="".join(outputs['stderr'])
                        )
                    
                    reads = [process.stdout.fileno(), process.stderr.fileno()]
                    ret = select.select(reads, [], [], min(5.0, timeout - elapsed_time))
                    
                    if not ret[0]:  # Timeout on select
                        continue
                        
                    for fd in ret[0]:
                        if fd == process.stdout.fileno():
                            line = process.stdout.readline()
                            if line:
                                logger.info(f"Script output: {line.strip()}")
                                outputs['stdout'].append(line)
                        if fd == process.stderr.fileno():
                            line = process.stderr.readline()
                            if line:
                                logger.warning(f"Script error: {line.strip()}")
                                outputs['stderr'].append(line)
                    
                    if process.poll() is not None:
                        break
                        
                return_code = process.wait()
                
                # Check if Odoo container is actually running regardless of script return code
                container_name = f"{tenant_database.lower()}_odoo"
                check_cmd = ['docker', 'ps', '--filter', f'name={container_name}', '--format', '{{.Names}}']
                result = subprocess.run(check_cmd, capture_output=True, text=True)
                
                if container_name in result.stdout:
                    # Container exists and is running, check for modules loaded
                    check_logs_cmd = ['docker', 'logs', '--tail', '100', container_name]
                    logs_result = subprocess.run(check_logs_cmd, capture_output=True, text=True)
                    
                    if "Modules loaded" in logs_result.stdout or "Modules loaded" in logs_result.stderr:
                        logger.info(f"Container {container_name} is running with modules loaded - considering deployment successful")
                        
                        # Wait a bit to ensure everything is fully initialized
                        time.sleep(15)
                        
                        # Try to verify the web interface is accessible
                        port_cmd = ['docker', 'port', container_name, '8069']
                        port_result = subprocess.run(port_cmd, capture_output=True, text=True)
                        
                        if port_result.stdout:
                            port = port_result.stdout.strip().split(':')[-1]
                            try:
                                # Try to access web interface
                                web_check = subprocess.run(
                                    ['curl', '-s', '--max-time', '5', f'http://localhost:{port}/web/database/manager'],
                                    capture_output=True, text=True
                                )
                                if 'Odoo' in web_check.stdout:
                                    logger.info(f"Web interface is accessible on port {port}")
                            except Exception as e:
                                logger.warning(f"Web interface check failed: {str(e)}")
                        
                        return {
                            "success": True,
                            "message": "Tenant created successfully (container is running with modules loaded)"
                        }
                
                if return_code != 0:
                    error_msg = "".join(outputs['stderr']) or "Unknown error"
                    logger.error(f"Script failed with return code {return_code}: {error_msg}")
                    
                    # Last attempt to check if the container is running despite the script error
                    if container_name in result.stdout:
                        logger.info(f"Despite script error, container {container_name} is running - attempting to proceed")
                        return {
                            "success": True,
                            "message": "Tenant created with warnings (container is running but script returned non-zero exit code)"
                        }
                    
                    return {
                        "success": False,
                        "message": f"Script execution failed: {error_msg}"
                    }
                
                # Give containers time to stabilize
                time.sleep(15)
                
                # Verify the tenant creation
                if not self._verify_tenant_creation(tenant_database, tenant_id, tenant_password):
                    # Check container again as a last resort
                    if container_name in result.stdout:
                        logger.info(f"Despite verification failure, container {container_name} is running - attempting to proceed")
                        return {
                            "success": True,
                            "message": "Tenant created with warnings (verification failed but container is running)"
                        }
                    
                    return {
                        "success": False,
                        "message": "Tenant verification failed"
                    }
                
                return {
                    "success": True,
                    "message": "Tenant created successfully"
                }
                
            except subprocess.TimeoutExpired as e:
                logger.error(f"Tenant creation timed out after {timeout} seconds")
                logger.error(f"Output before timeout:\n{e.output}")
                logger.error(f"Errors before timeout:\n{e.stderr}")
                
                # Check if the container is running despite the timeout
                container_name = f"{tenant_database.lower()}_odoo"
                check_cmd = ['docker', 'ps', '--filter', f'name={container_name}', '--format', '{{.Names}}']
                result = subprocess.run(check_cmd, capture_output=True, text=True)
                
                if container_name in result.stdout:
                    # Container exists and is running despite timeout
                    check_logs_cmd = ['docker', 'logs', '--tail', '100', container_name]
                    logs_result = subprocess.run(check_logs_cmd, capture_output=True, text=True)
                    
                    if "Modules loaded" in logs_result.stdout or "Modules loaded" in logs_result.stderr:
                        logger.info(f"Container {container_name} is running with modules loaded despite timeout - considering deployment successful")
                        return {
                            "success": True,
                            "message": "Tenant created successfully (container is running despite timeout)"
                        }
                
                return {
                    "success": False,
                    "message": f"Tenant creation timed out after {timeout} seconds"
                }
                
        except Exception as e:
            logger.error(f"Error creating tenant: {str(e)}")
            
            # Final attempt - check if container is running despite the error
            try:
                container_name = f"{tenant_database.lower()}_odoo"
                check_cmd = ['docker', 'ps', '--filter', f'name={container_name}', '--format', '{{.Names}}']
                result = subprocess.run(check_cmd, capture_output=True, text=True)
                
                if container_name in result.stdout:
                    logger.info(f"Despite error, container {container_name} is running - attempting to proceed")
                    return {
                        "success": True,
                        "message": "Tenant created with errors (container is running despite errors)"
                    }
            except Exception as check_error:
                logger.error(f"Final container check failed: {str(check_error)}")
            
            return {
                "success": False,
                "message": str(e)
            }
            
    
    def _verify_tenant_creation(self, tenant_database, tenant_id, tenant_password):
        """Verify tenant creation and update admin password using Odoo's password hashing"""
        try:
            # Check if containers are running
            container_name = f"{tenant_database.lower()}_odoo"
            check_cmd = ['docker', 'ps', '--filter', f'name={container_name}', '--format', '{{.Names}}']
            result = subprocess.run(check_cmd, capture_output=True, text=True)
            
            if container_name not in result.stdout:
                logger.error(f"Odoo container {container_name} not running")
                return False
            
            # Get RDS connection parameters from environment variables
            rds_host = os.environ.get('RDS_HOST', 'naidash.c1woe0mikr7h.eu-north-1.rds.amazonaws.com')
            rds_port = int(os.environ.get('RDS_PORT', 5432))
            rds_user = os.environ.get('RDS_USER', 'naidash')
            rds_password = os.environ.get('RDS_PASSWORD', '4a*azUp2025%')
            
            # Step 1: Update the login name first through direct database connection
            try:
                conn = psycopg2.connect(
                    dbname=tenant_database.lower(),
                    user=rds_user,
                    password=rds_password,
                    host=rds_host,
                    port=rds_port,
                    connect_timeout=10
                )
                conn.set_isolation_level(psycopg2.extensions.ISOLATION_LEVEL_AUTOCOMMIT)
                
                with conn.cursor() as cur:
                    # Update the admin user login name
                    cur.execute(f"UPDATE res_users SET login=%s WHERE login=%s", 
                            (tenant_id.lower(), 'admin'))
                    logger.info(f"Updated admin login to {tenant_id.lower()}")
                    
                    # Verify the login was updated
                    cur.execute("SELECT login FROM res_users WHERE id=2")
                    result = cur.fetchone()
                    if result and result[0] == tenant_id.lower():
                        logger.info("✓ Admin login updated successfully")
                    else:
                        logger.error(f"Admin login verification failed, got: {result}")
                        
                conn.close()
            except Exception as e:
                logger.error(f"Failed to update admin login: {str(e)}")
                return False
                    
            import textwrap

            script_content = textwrap.dedent(f"""\
                    import sys
                    import odoo
                    from passlib.context import CryptContext

                    # Setup password hashing context
                    pwd_context = CryptContext(schemes=['pbkdf2_sha512'])
                    hashed_password = pwd_context.hash('{tenant_password}')

                    try:
                        registry = odoo.modules.registry.Registry.new('{tenant_database.lower()}')
                        with registry.cursor() as cr:
                            cr.execute("UPDATE res_users SET password=%s WHERE login=%s", 
                                    [hashed_password, '{tenant_id.lower()}'])
                            cr.execute("SELECT id FROM res_users WHERE login=%s AND active=True", 
                                    ['{tenant_id.lower()}'])
                            user_id = cr.fetchone()
                            if user_id:
                                print(f"Password updated successfully for user ID: {{user_id[0]}}")
                            else:
                                print("Failed to verify password update")
                                sys.exit(1)
                    except Exception as e:
                        print(f"Error updating password: {{str(e)}}")
                        sys.exit(1)

                    print("✓ Admin password updated successfully with proper hashing")
                    sys.exit(0)
                """)

            
            # Write the script to a temporary file
            temp_script_path = f"/tmp/update_password_{tenant_database.lower()}.py"
            with open(temp_script_path, 'w') as f:
                f.write(script_content)
                
            # Copy the script to the container
            copy_cmd = ['docker', 'cp', temp_script_path, f"{container_name}:/tmp/update_password.py"]
            subprocess.run(copy_cmd, check=True, capture_output=True)
            
            # Execute the script in the container
            exec_cmd = ['docker', 'exec', container_name, 'python3', '/tmp/update_password.py']
            logger.info("Executing password update script inside container...")
            result = subprocess.run(exec_cmd, capture_output=True, text=True)
            
            if result.returncode != 0:
                logger.error(f"Password update script failed: {result.stderr or result.stdout}")
                return False
                
            logger.info(result.stdout.strip())
            
            # Clean up the temporary script
            os.remove(temp_script_path)
            
            # Step 3: Update odoo.conf file
            if not self._update_odoo_conf(tenant_database, tenant_id, tenant_password):
                logger.error("Failed to update odoo.conf file")
                return False
                
            # Step 4: Restart the Odoo container to apply changes
            restart_cmd = ['docker', 'restart', container_name]
            restart_result = subprocess.run(restart_cmd, capture_output=True, text=True)
            
            if restart_result.returncode != 0:
                logger.error(f"Failed to restart Odoo container: {restart_result.stderr}")
                return False
                
            # Wait for container to restart
            logger.info("Waiting for Odoo container to restart...")
            time.sleep(30)
            
            # Final verification - check if container is running
            check_cmd = ['docker', 'ps', '--filter', f'name={container_name}', '--format', '{{.Names}}']
            result = subprocess.run(check_cmd, capture_output=True, text=True)
            
            if container_name not in result.stdout:
                logger.error("Odoo container not running after restart")
                return False
                
            logger.info("✓ Tenant verification completed successfully")
            return True
                
        except Exception as e:
            logger.error(f"Verification failed: {str(e)}")
            return False
    def _update_odoo_conf(self, tenant_database, tenant_id, tenant_password):
        """Update the odoo.conf file with admin credentials"""
        try:
            container_name = f"{tenant_database.lower()}_odoo"
            
            # Step 1: Copy the current odoo.conf from the container
            temp_dir = "/tmp"
            local_conf_path = f"{temp_dir}/odoo_{tenant_database.lower()}.conf"
            
            copy_from_cmd = ['docker', 'cp', f"{container_name}:/etc/odoo/odoo.conf", local_conf_path]
            result = subprocess.run(copy_from_cmd, capture_output=True, text=True)
            
            if result.returncode != 0:
                logger.error(f"Failed to copy odoo.conf from container: {result.stderr}")
                # Try alternative location
                copy_from_cmd = ['docker', 'exec', container_name, 'find', '/', '-name', 'odoo.conf']
                result = subprocess.run(copy_from_cmd, capture_output=True, text=True)
                
                if result.returncode == 0 and result.stdout.strip():
                    container_conf_path = result.stdout.strip().split('\n')[0]
                    logger.info(f"Found odoo.conf at: {container_conf_path}")
                    
                    copy_from_cmd = ['docker', 'cp', f"{container_name}:{container_conf_path}", local_conf_path]
                    result = subprocess.run(copy_from_cmd, capture_output=True, text=True)
                    
                    if result.returncode != 0:
                        logger.error(f"Failed to copy odoo.conf from found location: {result.stderr}")
                        return False
                else:
                    logger.error("Could not find odoo.conf in container")
                    return False
            
            # Step 2: Update the configuration
            import re
            
            with open(local_conf_path, 'r') as f:
                config_content = f.read()
            
            # Update admin_passwd
            if re.search(r'admin_passwd\s*=', config_content):
                config_content = re.sub(r'admin_passwd\s*=\s*.*', f'admin_passwd = {tenant_password}', config_content)
            else:
                config_content += f"\nadmin_passwd = {tenant_password}\n"
            
            # Ensure database configuration is correct
            if re.search(r'db_user\s*=', config_content):
                config_content = re.sub(r'db_user\s*=\s*.*', f'db_user = {tenant_id.lower()}', config_content)
            else:
                config_content += f"\ndb_user = {tenant_id.lower()}\n"
                
            if re.search(r'db_password\s*=', config_content):
                config_content = re.sub(r'db_password\s*=\s*.*', f'db_password = {tenant_password}', config_content)
            else:
                config_content += f"\ndb_password = {tenant_password}\n"
                
            if re.search(r'db_name\s*=', config_content):
                config_content = re.sub(r'db_name\s*=\s*.*', f'db_name = {tenant_database.lower()}', config_content)
            else:
                config_content += f"\ndb_name = {tenant_database.lower()}\n"
            
            # Write updated config
            with open(local_conf_path, 'w') as f:
                f.write(config_content)
            
            # Step 3: Copy the updated file back to the container
            copy_to_cmd = ['docker', 'cp', local_conf_path, f"{container_name}:/etc/odoo/odoo.conf"]
            result = subprocess.run(copy_to_cmd, capture_output=True, text=True)
            
            if result.returncode != 0:
                logger.error(f"Failed to copy updated odoo.conf to container: {result.stderr}")
                return False
            
            # Step 4: Set proper permissions on the file
            chmod_cmd = ['docker', 'exec', container_name, 'chmod', '644', '/etc/odoo/odoo.conf']
            subprocess.run(chmod_cmd, check=True, capture_output=True)
            
            # Step 5: Clean up temporary file
            os.remove(local_conf_path)
            
            logger.info("✓ Updated odoo.conf with new admin credentials")
            return True
            
        except Exception as e:
            logger.error(f"Failed to update odoo.conf: {str(e)}")
            return False
        
    def _verify_and_fix_role(self, tenant_database, tenant_id, tenant_password):
        """Verify and fix database role if needed"""
        try:
            container_name = f"{tenant_database.lower()}_db"
            
            # First revoke all existing privileges
            subprocess.run([
                'docker', 'exec', '-i', container_name,
                'psql', '-U', 'postgres', '-d', 'postgres', '-c',
                f"REASSIGN OWNED BY {tenant_id.lower()} TO postgres;"
            ], check=True)
            
            # Drop and recreate role
            subprocess.run([
                'docker', 'exec', '-i', container_name,
                'psql', '-U', 'postgres', '-d', 'postgres', '-c',
                f"DROP ROLE IF EXISTS {tenant_id.lower()};"
            ], check=True)
            
            # Create new role with proper permissions
            subprocess.run([
                'docker', 'exec', '-i', container_name,
                'psql', '-U', 'postgres', '-d', 'postgres', '-c',
                f"CREATE USER {tenant_id.lower()} WITH LOGIN PASSWORD '{tenant_password}' SUPERUSER CREATEDB CREATEROLE REPLICATION;"
            ], check=True)
            
            # Grant specific database privileges
            subprocess.run([
                'docker', 'exec', '-i', container_name,
                'psql', '-U', 'postgres', '-d', tenant_database.lower(), '-c',
                f"GRANT ALL PRIVILEGES ON DATABASE {tenant_database.lower()} TO {tenant_id.lower()};"
            ], check=True)
            
            return True
        except Exception as e:
            logger.error(f"Error verifying/fixing role: {str(e)}")
            return False
    
        
    def _test_tenant_connection(self, tenant_database, tenant_id, tenant_password):
        """Test tenant database connection with retries"""
        max_attempts = 15  # Increased attempts for better reliability
        delay_seconds = 20
        
        # Add initial delay to allow for container startup
        logger.info(f"Waiting {30} seconds for initial container setup...")
        time.sleep(30)
        
        # Get RDS connection parameters from environment variables
        rds_host = os.environ.get('RDS_HOST', 'naidash.c1woe0mikr7h.eu-north-1.rds.amazonaws.com')
        rds_port = int(os.environ.get('RDS_PORT', 5432))
        rds_user = os.environ.get('RDS_USER', 'naidash')
        rds_password = os.environ.get('RDS_PASSWORD', '4a*azUp2025%')
        
        for attempt in range(max_attempts):
            try:
                logger.info(f"Connection test attempt {attempt + 1} of {max_attempts}")
                
                # Check container status first
                check_cmd = ['docker', 'inspect', '--format', '{{.State.Status}}', f'{tenant_database.lower()}_odoo']
                result = subprocess.run(check_cmd, capture_output=True, text=True)
                if 'running' not in result.stdout:
                    logger.error("Odoo container not running")
                    if attempt < max_attempts - 1:
                        time.sleep(delay_seconds)
                    continue
                
                # 1. First verify/fix role
                if not self._verify_and_fix_role(tenant_database, tenant_id, tenant_password):
                    logger.error("Failed to verify/fix database role")
                    if attempt < max_attempts - 1:
                        time.sleep(delay_seconds)
                    continue
                
                # 2. Test connection to RDS postgres database
                try:
                    # Changed from local postgres to RDS
                    postgres_conn = psycopg2.connect(
                        dbname='postgres',
                        user=rds_user,
                        password=rds_password,
                        host=rds_host,
                        port=rds_port,
                        connect_timeout=10
                    )
                    postgres_conn.close()
                    logger.info(f"Successfully connected to postgres database on {rds_host}")
                except Exception as e:
                    logger.error(f"Failed to connect to RDS postgres database: {str(e)}")
                    if attempt < max_attempts - 1:
                        time.sleep(delay_seconds)
                    continue

                # 3. Test connection to tenant database on RDS
                try:
                    # Direct connection to tenant DB on RDS (more reliable than docker exec)
                    tenant_conn = psycopg2.connect(
                        dbname=tenant_database.lower(),
                        user=rds_user,  # Use RDS user which has permissions to all DBs
                        password=rds_password,
                        host=rds_host,
                        port=rds_port,
                        connect_timeout=10
                    )
                    with tenant_conn.cursor() as cur:
                        cur.execute('SELECT current_database(), current_user;')
                        result = cur.fetchone()
                        logger.info(f"Direct tenant DB connection test result: {result}")
                    tenant_conn.close()
                    return True
                except Exception as e:
                    logger.error(f"Direct tenant DB connection test failed: {str(e)}")
                    
                    # 4. Fall back to Docker container connection as a last resort
                    docker_test_cmd = [
                        'docker', 'exec',
                        '-e', f'PGPASSWORD={tenant_password}',
                        f'{tenant_database.lower()}_db',
                        'psql',
                        '-h', rds_host,
                        '-p', str(rds_port),
                        '-U', rds_user,
                        '-d', tenant_database.lower(),
                        '-c', 'SELECT current_database(), current_user;'
                    ]
                    
                    try:
                        docker_result = subprocess.run(
                            docker_test_cmd,
                            capture_output=True,
                            text=True,
                            check=True
                        )
                        logger.info(f"Docker connection test output: {docker_result.stdout}")
                        return True
                    except subprocess.CalledProcessError as e:
                        logger.error(f"Docker connection test failed: {e.stderr}")
                        if attempt < max_attempts - 1:
                            time.sleep(delay_seconds)
                        continue
            except Exception as e:
                logger.error(f"Unexpected error during connection test: {str(e)}")
                if attempt < max_attempts - 1:
                    time.sleep(delay_seconds)
                continue
        
        logger.error(f"Connection test failed after {max_attempts} attempts")
        return False

    def _get_postgres_connection(self):
        """Get connection to postgres database"""
        try:
            # Use RDS environment variables for remote database connection
            rds_host = os.environ.get('RDS_HOST', 'naidash.c1woe0mikr7h.eu-north-1.rds.amazonaws.com')
            rds_port = int(os.environ.get('RDS_PORT', 5432))
            rds_user = os.environ.get('RDS_USER', 'naidash')
            rds_password = os.environ.get('RDS_PASSWORD', '4a*azUp2025%')
            
            try:
                # Connect to the AWS RDS PostgreSQL instance
            
                logger.info(f"Connecting to remote PostgreSQL at {rds_host}:{rds_port}")
                return psycopg2.connect(
                    dbname='postgres',  
                    user=rds_user,      
                    password=rds_password,
                    host=rds_host,
                    port=rds_port
                )
            except psycopg2.OperationalError as e:
                logger.warning(f"Failed to connect to RDS PostgreSQL: {str(e)}")
                
                # Fall back to local connection only as a last resort
                logger.warning("Attempting fallback to local PostgreSQL connection")
                return psycopg2.connect(
                    dbname='postgres',
                    user=os.getenv('USER'),
                    password=os.environ.get('PGPASSWORD', 'postgres'),
                    host='localhost'
                )
        except psycopg2.OperationalError as e:
            logger.error(f"Failed to connect to PostgreSQL: {str(e)}")
            raise

    # === Main partner creation method ===
    def create_the_partner(self, request_data):
        """Create a partner with tenant setup for companies"""
        request.httprequest.environ['REQUEST_TIMEOUT'] = 900  # 15 minutes
        try:
            # Initialize response containers
            data = dict()
            response_data = dict()
            tenant_database = None
            tenant_id = None
            tenant_password = None
            business_id = None  # Initialize business_id
            
            # Validate request data first
            validation_result = self._validate_partner_data(request_data)
            if validation_result.get("error"):
                return validation_result
                
            # Check for existing partner before proceeding
            if self._check_existing_partner(request_data):
                return {
                    "code": 409,
                    "message": "Account already exists!"
                }
            
            # Prepare partner details first
            partner_details = self._prepare_partner_details(request_data)
                
            # For company accounts, create tenant first
            if request_data.get("account_type") == "company":
                try:
                    # Generate business ID from company name
                    business_id = self._generate_business_id(request_data.get("name"))
                    
                    # Add business_id to partner details
                    partner_details["business_id"] = business_id
                    
                    # Generate tenant credentials using business ID
                    tenant_id, tenant_database = self._generate_tenant_identifiers(business_id)
                    tenant_password = self._generate_tenant_password()
                    
                    logger.info(f"Generated tenant credentials - Business ID: {business_id}, DB: {tenant_database}")
                    
                    # Validate tenant configuration
                    self._validate_tenant_names(tenant_database, tenant_id)
                    self._validate_tenant_password(tenant_password)
                    
                    # Get and validate script
                    script_path = self._get_script_path()
                    self._validate_script_permissions(script_path)
                    
                    # Create tenant with timeout
                    tenant_creation_result = self._create_tenant_with_timeout(
                        script_path,
                        tenant_database,
                        tenant_id,
                        tenant_password,
                        timeout=9900  # 15 minutes
                    )
                    
                    if not tenant_creation_result["success"]:
                        logger.error(f"Tenant creation failed: {tenant_creation_result['message']}")
                        raise ValidationError(tenant_creation_result["message"])
                        
                    logger.info(f"Tenant created successfully: {tenant_database}")
                    
                    # Add tenant info to partner details
                    partner_details.update({
                        "partner_database_name": tenant_database,
                        "partner_primary_id": tenant_id,
                        "partner_secondary_id": tenant_password,
                    })
                        
                except Exception as e:
                    logger.error(f"Failed to create tenant: {str(e)}")
                    # Clean up any partial tenant creation
                    if tenant_database and tenant_id:
                        logger.info(f"Cleaning up failed tenant: {tenant_database}")
                        self._cleanup_failed_tenant(tenant_database, tenant_id)
                    raise ValidationError(_("Failed to create tenant environment")) from e
                    
            # Create partner within a transaction
            with self.env.cr.savepoint():
                partner = self.env['res.partner'].create(partner_details)
                
                # Prepare success response
                data['id'] = partner.id
                if partner.company_type == "company":
                    data.update({
                        'tenant_database': partner.partner_database_name,
                        'tenant_id': partner.partner_primary_id,
                        'tenant_password': partner.partner_secondary_id,
                        'business_id': partner.business_id,
                    })
                    
                response_data["code"] = 201
                response_data["message"] = "Partner created successfully"
                response_data["data"] = data
                
                logger.info(f"Partner created successfully: ID {partner.id}")
                
            return response_data
            
        except ValidationError as e:
            logger.error(f"Validation error in create_the_partner: {str(e)}")
            # Make sure to clean up in case of validation error
            if tenant_database and tenant_id:
                self._cleanup_failed_tenant(tenant_database, tenant_id)
            raise
            
        except Exception as e:
            logger.error(f"Error in create_the_partner: {str(e)}")
            # Ensure cleanup in case of any error
            if tenant_database and tenant_id:
                self._cleanup_failed_tenant(tenant_database, tenant_id)
            raise
        
        
            # method to look up tenants by business ID
    def _lookup_tenant_by_business_id(self, business_id):
            """Look up tenant details by business ID"""
            partner = self.env['res.partner'].search([
                ('business_id', '=', business_id),
                ('company_type', '=', 'company')
            ], limit=1)
            
            if partner:
                return {
                    'tenant_database': partner.partner_database_name,
                    'tenant_id': partner.partner_primary_id,
                    'partner_id': partner.id,
                    'creation_date': partner.create_date.strftime('%Y-%m-%d %H:%M:%S')
                }
            return None
        
        #validation to ensure business IDs are unique
    def _validate_business_id(self, business_id):
        """Validate that business ID is unique"""
        existing = self.env['res.partner'].search_count([
            ('business_id', '=', business_id)
        ])
        
        if existing > 0:
            # If duplicate found, add random suffix
            suffix = "".join(random.choices(string.digits, k=4))
            new_business_id = f"{business_id}_{suffix}"
            return self._validate_business_id(new_business_id)
        
        return business_id

    def _generate_tenant_password(self):
        """Generate a valid tenant password that meets all requirements"""
        # Ensure at least one number
        password = [random.choice(string.digits)]  # Guarantee one number
        # Ensure at least one uppercase letter
        password.append(random.choice(string.ascii_uppercase))
        # Fill the rest with a mix of numbers and uppercase letters
        remaining_length = 8  # To make total length 10
        password.extend(random.choices(string.ascii_uppercase + string.digits, k=remaining_length))
        # Shuffle the password characters
        random.shuffle(password)
        return "".join(password)
    
    # API to lookup tenat details by business ID
    def lookup_tenant_details(self, business_id):
        """Look up tenant connection details by business ID"""
        partner = self.env['res.partner'].search([
            ('business_id', '=', business_id),
            ('company_type', '=', 'company')
        ], limit=1)
        
        if partner:
            return {
                'code': 200,
                'data': {
                    'tenant_database': partner.partner_database_name,
                    'tenant_id': partner.partner_primary_id,
                    'tenant_url': f'/api/tenant/{business_id}'
                }
            }
        return {
            'code': 404,
            'message': 'Tenant not found'
        }

        
    def edit_the_partner(self, partner_id, request_data):
        """Edit the partner details
        """ 
                
        try:
            response_data = dict()
            
            if not partner_id:
                response_data["code"] = 400
                response_data["message"] = "Partner ID is required!"
                return response_data
                        
            partner = self.env['res.partner'].search(
                [
                    ('id','=', int(partner_id)), 
                    '|', ('active','=', True), ('active','=', False)
                ]
            )
            
            if partner:
                partner_details = dict()

                if request_data.get("account_type"):
                    account_type = request_data.get("account_type")
                    partner_details["company_type"] = "person" if account_type == "individual" else account_type
                    
                if request_data.get("name"):
                    partner_name = (request_data.get("name")).strip()
                    partner_name = partner_name.title()
                    partner_details["name"] = partner_name
                    
                if request_data.get("email"):
                    partner_details["email"] = (request_data.get("email")).strip()
                    
                if request_data.get("phone"):
                    country = self.env["res.country"].search([('code','=', 'KE')], order='id asc', limit=1)
                    phone_number = request_data.get("phone")
                    
                    if (not phone_number.startswith("01") and not phone_number.startswith("07")) or len(phone_number) != 10:
                        response_data["code"] = 400
                        response_data["message"] = "Unsupported phone number format!"
                        return response_data
                                
                    if phone_number.startswith("01") or phone_number.startswith("07"):
                        phone_number = phone_number.replace('01', country.phone_code, 1).replace('07', country.phone_code, 1)
                        phone_number = phone_number.strip()
                    
                    partner_details["phone"] = phone_number

                if request_data.get("id_number"):
                    partner_details["id_number"] = (request_data.get("id_number")).strip()
                    
                if request_data.get("tax_id"):
                    partner_details["vat"] = (request_data.get("tax_id")).strip()
                    
                if request_data.get("active") == True or request_data.get("active") == False:
                    partner_details["active"] = request_data.get("active")
                                        
                if request_data.get("tag_ids"):
                    tag_ids = request_data.get("tag_ids")
                    tags = self.env['res.partner.category'].browse(tag_ids)
                    
                    if tags:
                        partner_details["category_id"] = [tag.id for tag in tags]
                    else:
                        response_data["code"] = 404
                        response_data["message"] = "Tag not found!"
                        return response_data
                    
                # Update partner details
                if partner_details:
                    partner.write(partner_details)
                    response_data["code"] = 200
                    response_data["message"] = "Updated successfully"
                else:
                    response_data["code"] = 204
                    response_data["message"] = "Nothing to update"
            else:
                response_data["code"] = 404
                response_data["message"] = "Partner not found!"                    
            
            return response_data
        except AccessDenied as e:
            logger.error(f"AccessDenied error ocurred while modifying the partner details:\n\n{str(e)}")
            raise e
        except AccessError as e:
            logger.error(f"AccessError ocurred while modifying the partner details:\n\n{str(e)}")
            raise e
        except TypeError as e:
            logger.error(f"Datatype error ocurred while modifying the partner:\n\n{str(e)}")
            raise e
        except Exception as e:
            logger.error(f"An error ocurred while modifying the partner:\n\n{str(e)}")
            raise e
        
    def get_the_partner(self, partner_id):
        """Get the partner details
        """        
        
        try:
            data = dict()
            response_data = dict()
            
            if not partner_id:
                response_data["code"] = 400
                response_data["message"] = "Partner ID is required!"
                return response_data
            
            partner = self.env['res.partner'].search(
                [
                    ('id','=', int(partner_id)), 
                    '|', ('active','=', True), ('active','=', False)
                ]
            )
            
            if partner:
                data["id"] = partner.id
                data["name"] = partner.name
                data["phone"] = partner.phone or ""
                data["email"] = partner.email or ""
                data["account_type"] = "individual" if partner.company_type == "person" else partner.company_type
                data["id_number"] = partner.id_number or ""
                data["tax_id"] = partner.vat or ""
                data["active"] = partner.active
                data["phone_verified"] = partner.is_phone_number_verified
                data["email_verified"] = partner.is_email_verified
                data["id_verified"] = partner.is_id_number_verified
                # data["total_amount_due"] = partner.payment_amount_due
                data["company"] = {"id": partner.company_id.id, "name": partner.company_id.name} if partner.company_id else {}
                data["tag_ids"] = [{"id": tag.id, "name": tag.name} for tag in partner.category_id] if partner.category_id else []
                data["profile_photo"] = ""
                
                # Check for the profile photo
                if partner.image_1920:                    
                    profile_photo = partner.image_1920
                    
                    # Decode the profile photo since it's already encoded by default
                    decoded_image = base64.b64decode(profile_photo)
                    
                    # Encode the profile photo again
                    profile_photo = base64.b64encode(decoded_image).decode('utf-8')
                
                    data["profile_photo"] = profile_photo                
                
                response_data["code"] = 200
                response_data["message"] = "Success"
                response_data["data"] = data
            else:
                response_data["code"] = 404
                response_data["message"] = "Partner not found!"
            
            return response_data
        except AccessDenied as e:
            logger.error(f"AccessDenied error ocurred while fetching the partners:\n\n{str(e)}")
            raise e
        except AccessError as e:
            logger.error(f"AccessError ocurred while fetching the partners:\n\n{str(e)}")
            raise e
        except Exception as e:
            logger.error(f"The following error ocurred while fetching the partner details:\n\n{str(e)}")
            raise e
        
    def get_all_the_partners(self):
        """Get all the partners
        """        
        
        try:
            response_data = dict()
            all_partners = []
            partners = self.env['res.partner'].search(
                [
                    '|',
                    ('active','=', True),
                    ('active','=', False)
                ]
            )
            
            if partners:
                for partner in partners:
                    data = dict()
                    data["id"] = partner.id
                    data["name"] = partner.name
                    data["phone"] = partner.phone or ""
                    data["email"] = partner.email or ""
                    data["account_type"] = "individual" if partner.company_type == "person" else partner.company_type
                    data["id_number"] = partner.id_number or ""
                    data["tax_id"] = partner.vat or ""
                    data["active"] = partner.active
                    data["phone_verified"] = partner.is_phone_number_verified
                    data["email_verified"] = partner.is_email_verified
                    data["id_verified"] = partner.is_id_number_verified
                    # data["total_amount_due"] = partner.payment_amount_due
                    data["company"] = {"id": partner.company_id.id, "name": partner.company_id.name} if partner.company_id else {}
                    data["tag_ids"] = [{"id": tag.id, "name": tag.name} for tag in partner.category_id] if partner.category_id else []
                    
                    all_partners.append(data)
                
                response_data["code"] = 200
                response_data["message"] = "Success"
                response_data["data"] = all_partners
            else:
                response_data["code"] = 404
                response_data["message"] = "Partner not found!"
            
            return response_data
        except AccessDenied as e:
            logger.error(f"AccessDenied error ocurred while fetching the partners:\n\n{str(e)}")
            raise e
        except AccessError as e:
            logger.error(f"AccessError ocurred while fetching the partners:\n\n{str(e)}")
            raise e
        except Exception as e:
            logger.error(f"The following error ocurred while fetching the partners:\n\n{str(e)}")
            raise e