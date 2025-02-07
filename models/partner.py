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
import odoo_master



# try:
#     import africastalking
#     from africastalking.Service import AfricasTalkingException
#     from odoo_master import c
# except ImportError:
#     msg = _('Install the "africastalking" package i.e `pip3 install africastalking`')
#     raise ValidationError(msg)

logger = logging.getLogger(__name__)

class NaidashPartner(models.Model):
    _inherit = "res.partner"
    
    
    id_number = fields.Char(string="Identification No.")
    partner_primary_id = fields.Char(string="Partner's Primary ID")
    partner_secondary_id = fields.Char(string="Partner's Secondary ID")
    partner_database_name = fields.Char(string="Partner's Database Name")
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
    ###
    def _get_script_path(self):
        """Get and validate tenant creation script path"""
        try:
            # Get module path and navigate to root directory
            current_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # naidash_auth_master
            root_dir = os.path.dirname(os.path.dirname(current_dir))  # odoo_master
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
        
    def _validate_tenant_names(self, tenant_database, tenant_id):
        """Validate tenant naming conventions"""
        import re
        
        for name in [tenant_database, tenant_id]:
            if not re.match(r'^[A-Z][A-Z0-9_]{3,63}$', name):
                raise ValidationError(_(
                    "Invalid tenant name format. Must start with uppercase letter and "
                    "contain only uppercase letters, numbers, and underscores"
                ))
            
            # Check reserved names
            reserved = ['POSTGRES', 'TEMPLATE0', 'TEMPLATE1', 'ODOO']
            if name.upper() in reserved:
                raise ValidationError(_("Reserved name cannot be used for tenant"))
        
        return True

    def _validate_tenant_password(self, password):
        """Validate tenant password strength"""
        if len(password) < 10:
            raise ValidationError(_("Password must be at least 10 characters"))
            
        if not any(c.isupper() for c in password):
            raise ValidationError(_("Password must contain uppercase letters"))
            
        if not any(c.isdigit() for c in password):
            raise ValidationError(_("Password must contain numbers"))
            
        return True
        
    def _get_db_connection(self, db_name):
        """Get database connection for tenant"""
        try:
            db = registry(db_name)
            with db.cursor() as cr:
                return cr
        except Exception as e:
            logger.error(f"Database connection error: {str(e)}")
            raise

    def _test_tenant_connection(self, tenant_database, tenant_id, tenant_password):
        """Test tenant database connection"""
        try:
            # Give the database more time to initialize
            time.sleep(15)
            
            # Test postgres connection first using default postgres credentials
            try:
                conn = psycopg2.connect(
                    dbname='postgres',
                    user='postgres',
                    password='postgres',
                    host='localhost'
                )
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT 1 FROM pg_roles WHERE rolname = %s",
                        (tenant_id.lower(),)
                    )
                    if not cur.fetchone():
                        logger.info(f"Role {tenant_id.lower()} not found in postgres")
                        return False
                conn.close()
            except Exception as e:
                logger.error(f"Error checking postgres role: {str(e)}")
                return False

            # Test tenant database connection
            try:
                conn = psycopg2.connect(
                    dbname=tenant_database.lower(),
                    user=tenant_id.lower(),
                    password=tenant_password,
                    host='localhost'
                )
                with conn.cursor() as cur:
                    cur.execute('SELECT 1')
                conn.close()
                return True
            except Exception as e:
                logger.error(f"Error connecting to tenant database: {str(e)}")
                return False
                    
        except Exception as e:
            logger.error(f"Tenant connection test failed: {str(e)}")
            return False
    
    def _cleanup_failed_tenant(self, tenant_database, tenant_id):
        """Clean up resources if tenant creation fails"""
        try:
            # Directory cleanup
            current_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            root_dir = os.path.dirname(os.path.dirname(current_dir))
            tenant_dir = os.path.join(root_dir, 'tenants', tenant_database.lower())
            
            logger.info(f"Cleaning up tenant directory: {tenant_dir}")
            
            if os.path.exists(tenant_dir):
                try:
                    subprocess.run(
                        ['docker-compose', 'down'],
                        cwd=tenant_dir,
                        check=True,
                        capture_output=True
                    )
                    logger.info("Docker containers stopped successfully")
                except subprocess.CalledProcessError as e:
                    logger.error(f"Failed to stop containers: {e.stderr}")
                
                try:
                    shutil.rmtree(tenant_dir)
                    logger.info("Tenant directory removed successfully")
                except OSError as e:
                    logger.error(f"Failed to remove tenant directory: {e}")
            
            # Database cleanup
            try:
                with self.env.cr.savepoint():
                    # Terminate existing connections
                    self.env.cr.execute("""
                        SELECT pg_terminate_backend(pid) 
                        FROM pg_stat_activity 
                        WHERE datname = %s
                    """, (tenant_database.lower(),))
                    
                    # Drop database without quotes
                    self.env.cr.execute("""
                        DROP DATABASE IF EXISTS {}
                    """.format(tenant_database.lower()))
                    
                    # Drop role without quotes
                    self.env.cr.execute("""
                        DROP ROLE IF EXISTS {}
                    """.format(tenant_id.lower()))
                    
                logger.info(f"Database cleanup completed for tenant: {tenant_database}")
            except Exception as e:
                logger.error(f"Database cleanup failed: {str(e)}")
                
        except Exception as e:
            logger.error(f"Cleanup failed: {str(e)}")
    
    def create_the_partner(self, request_data):
        """Create a partner with tenant setup for companies"""
        try:
            data = dict()
            response_data = dict()
            query_params = ['|']
            
            partner_name = request_data.get("name")
            email = request_data.get("email")
            phone_number = request_data.get("phone")
            id_number = request_data.get("id_number")
            tax_id = request_data.get("tax_id")
            account_type = request_data.get("account_type")
            tag_ids = request_data.get("tag_ids")
            
            # Basic validation
            if account_type not in ["individual", "company"]:
                response_data["code"] = 400
                response_data["message"] = "Account type must be either 'individual' or 'company'"
                return response_data
            
            if not partner_name:
                response_data["code"] = 400
                response_data["message"] = "Name is required!"
                return response_data
            
            if not phone_number:
                response_data["code"] = 400
                response_data["message"] = "Phone number is required!"
                return response_data
            
            if (not phone_number.startswith("01") and not phone_number.startswith("07")) or len(phone_number) != 10:
                response_data["code"] = 400
                response_data["message"] = "Unsupported phone number format!"
                return response_data
            
            if not isinstance(tag_ids, list):
                response_data["code"] = 422
                response_data["message"] = "Expected a list of integer(s) in `tag_ids`"
                return response_data
            
            # Email duplication check
            if email:
                query_params.append('|')
                query_params.append(("email", "ilike", email))
                
            
            # Phone number formatting
            country = self.env["res.country"].search([('code','=', 'KE')], order='id asc', limit=1)
            if phone_number.startswith("01") or phone_number.startswith("07"):
                country_code = str(country.phone_code)
                phone_number = phone_number.replace('01', country_code, 1).replace('07', country_code, 1)
                phone_number = phone_number.strip()
            
            # Check for existing partner
            query_params.append(('phone','=', phone_number))
            query_params.append(('mobile','=', phone_number))
            
            admin = self.env["res.users"].search([], order='id asc', limit=1)
            partner_account = self.env["res.partner"].search(query_params, order='id asc', limit=1)
            
            if partner_account:
                response_data["code"] = 409
                response_data["message"] = "Account already exists!"
                return response_data
            
            # Prepare partner details
            partner_details = {
                "company_type": "person" if account_type == "individual" else account_type,
                "name": (partner_name.strip()).title(),
                "phone": phone_number,
                "email": email.strip() if email else "",
                "id_number": id_number.strip() if id_number else "",
                "vat": tax_id.strip() if tax_id else "",
                "country_id": country.id,
                "company_id": admin.company_id.id,
                "tz": "Africa/Nairobi"
            }
            
            # Handle tags
            if tag_ids:
                tags = self.env['res.partner.category'].browse(tag_ids)
                if tags:
                    partner_details["category_id"] = [tag.id for tag in tags]
                else:
                    response_data["code"] = 404
                    response_data["message"] = "Tag not found!"
                    return response_data
            
            # Create partner
            partner = self.env['res.partner'].create(partner_details)
            
            if partner:
                # Handle company-type partners with tenant creation
                if partner.company_type == "company":
                    try:
                        # Generate tenant credentials
                        tenant_id = "TID_" + "".join(random.choices(string.ascii_uppercase + string.digits, k=10))
                        tenant_database = "TDB_" + ''.join(random.choices(string.ascii_uppercase + string.digits, k=10))
                        tenant_password = "".join(random.choices(string.ascii_uppercase + string.digits, k=10))
                        
                        # Validate tenant names and credentials
                        self._validate_tenant_names(tenant_database, tenant_id)
                        self._validate_tenant_password(tenant_password)
                        
                        # Get and validate script
                        script_path = self._get_script_path()
                        self._validate_script_permissions(script_path)
                        
                        # Update partner with tenant info
                        partner.sudo().write({
                            "partner_database_name": tenant_database,
                            "partner_primary_id": tenant_id,
                            "partner_secondary_id": tenant_password,
                        })
                        
                        # Execute tenant creation
                        try:
                            args = [tenant_database.lower(), tenant_id.lower(), tenant_password]
                            logger.info(f"Executing tenant creation script with args: {args}")
                            env = os.environ.copy()
                            env['PGPASSWORD'] = 'postgres'
                            
                            process = subprocess.run(
                                [script_path] + args,
                                capture_output=True,
                                text=True,
                                check=True,
                                env=env
                            )
                            logger.info(f"Script output: {process.stdout}")
                            
                            # Give some time for the database to be fully ready
                            time.sleep(10)
                            
                            # Test tenant connection
                            if not self._test_tenant_connection(tenant_database, tenant_id, tenant_password):
                                raise ValidationError(_("Tenant creation failed - connection test failed"))
                                
                        except subprocess.CalledProcessError as e:
                            logger.error(f"Tenant creation script failed: {e.stderr}")
                            self._cleanup_failed_tenant(tenant_database, tenant_id)
                            raise ValidationError(_("Failed to create tenant environment")) from e
                            
                    except Exception as e:
                        logger.error(f"Tenant creation failed: {str(e)}")
                        # Attempt cleanup if tenant info was created
                        if tenant_database and tenant_id:
                            self._cleanup_failed_tenant(tenant_database, tenant_id)
                        raise ValidationError(_("Failed to create tenant environment")) from e
                
                # Prepare success response
                data['id'] = partner.id
                if partner.company_type == "company":
                    data.update({
                        'tenant_database': partner.partner_database_name,
                        'tenant_id': partner.partner_primary_id,
                        'tenant_password': partner.partner_secondary_id
                    })
                
                response_data["code"] = 201
                response_data["message"] = "Partner created successfully"
                response_data["data"] = data
            
            return response_data
        
        except AccessDenied as e:
            logger.error(f"AccessDenied error occurred while creating the partner:\n\n{str(e)}")
            raise
        except AccessError as e:
            logger.error(f"AccessError occurred while creating the partner:\n\n{str(e)}")
            raise
        except ValidationError as e:
            logger.error(f"Validation error occurred while creating the partner:\n\n{str(e)}")
            response_data = {
                "code": 400,
                "message": str(e)
            }
            return response_data
        except Exception as e:
            logger.error(f"An error occurred while creating the partner:\n\n{str(e)}")
            raise
        
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
                data["total_amount_due"] = partner.payment_amount_due
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
                    data["total_amount_due"] = partner.payment_amount_due
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