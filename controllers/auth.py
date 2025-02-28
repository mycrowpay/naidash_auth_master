import json
import logging
import odoo
import psycopg2

from odoo import http
from odoo.http import request, route, Response
from odoo.exceptions import AccessError, UserError, AccessDenied
from datetime import datetime, timedelta

# Properly import the model
from ..models.auth import NaidashAuth as NaidashAuthModel

logger = logging.getLogger(__name__)
naidash_auth = NaidashAuthModel()

class NaidashAuthController(http.Controller):
    @route('/api/v1/auth/login', methods=['POST', 'OPTIONS'], type='http', auth="none", csrf=False, cors="*")
    def login(self, **kw):
        """Handle login requests and OPTIONS for CORS"""
        headers = {
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Credentials': 'true',
            'Access-Control-Allow-Methods': 'POST, OPTIONS',
            'Access-Control-Allow-Headers': 'Content-Type, Authorization, X-Business-ID'
        }
        
        # Handle OPTIONS request for CORS
        if request.httprequest.method == 'OPTIONS':
            return Response(status=200, headers=headers)
        
        # Handle POST request for login
        try:
            # Parse the request body
            data = json.loads(request.httprequest.data.decode('utf-8'))
            
            # Extract data from JSON-RPC format
            params = data.get('params', {})
            login = params.get('login')
            password = params.get('password')
            
            if not login or not password:
                return Response(
                    json.dumps({
                        "jsonrpc": "2.0",
                        "id": data.get('id', None),
                        "error": {
                            "code": 400,
                            "message": "Login and password are required"
                        }
                    }),
                    status=400,
                    content_type='application/json',
                    headers=headers
                )
            
            # Get database name from environment
            db = request.env.cr.dbname
            logger.info(f"Attempting login for {login} on database {db}")
            
            # For tenant login, we need to connect to the tenant's database instead
            business_id = request.httprequest.headers.get('X-Business-ID')
            
            if business_id:
                logger.info(f"Tenant login attempt for business ID: {business_id}")
                
                # Get tenant details from partner data
                try:
                    partner = request.env['res.partner'].sudo().search([
                        ('business_id', '=', business_id),
                        ('company_type', '=', 'company')
                    ], limit=1)
                    
                    if partner:
                        tenant_database = partner.partner_database_name
                        tenant_id = partner.partner_primary_id
                        logger.info(f"Found tenant database: {tenant_database}, tenant ID: {tenant_id}")
                        
                        # Use direct database connection for tenant database
                        try:
                            # Check if we're trying to directly authenticate to tenant container
                            check_tenant_container = self._try_direct_tenant_auth(
                                tenant_database.lower(), 
                                login, 
                                password, 
                                business_id
                            )
                            
                            if check_tenant_container.get('success'):
                                # Direct container auth succeeded
                                return Response(
                                    json.dumps({
                                        "jsonrpc": "2.0",
                                        "id": data.get('id', None),
                                        "result": check_tenant_container.get('data')
                                    }),
                                    status=200,
                                    content_type='application/json',
                                    headers=headers
                                )
                        except Exception as e:
                            logger.error(f"Error trying direct tenant authentication: {str(e)}")
                    else:
                        logger.error(f"Tenant partner not found for business ID: {business_id}")
                except Exception as e:
                    logger.error(f"Error finding tenant details: {str(e)}")
            
            try:
                # Try authentication on current database
                pre_uid = request.session.authenticate(db, login, password)        
                        
                if pre_uid != request.session.uid:
                    return Response(
                        json.dumps({
                            "jsonrpc": "2.0",
                            "id": data.get('id', None),
                            "result": {"user_id": None}
                        }),
                        status=401,
                        content_type='application/json',
                        headers=headers
                    )

                # Setup session
                request.session.db = db
                registry = odoo.modules.registry.Registry(db)
                with registry.cursor() as cr:
                    env = odoo.api.Environment(cr, request.session.uid, request.session.context)
                    if not request.db and not request.session.is_explicit:
                        # Rotate the session
                        http.root.session_store.rotate(request.session, env)
                        
                        # Set session cookie
                        cookie_expiry_date = datetime.now() + timedelta(hours=2)
                        request.future_response.set_cookie(
                            'session_id', request.session.sid,
                            max_age=http.SESSION_LIFETIME, httponly=True,
                            expires=cookie_expiry_date
                        )
                    
                    # Get user info
                    user = request.env.user
                    results = env['ir.http'].session_info()
                    
                    # Prepare success response
                    response_data = {
                        "jsonrpc": "2.0",
                        "id": data.get('id', None),
                        "result": {
                            "code": 200,
                            "message": "Logged in successfully",
                            "data": {
                                "id": results.get("uid"),
                                "uid": results.get("uid"),
                                "username": login,
                                "partner_id": results.get("partner_id"),
                                "name": user.name,
                                "is_admin": user.has_group('base.group_system'),
                                "is_system": user.has_group('base.group_system'),
                                "user_context": results.get("user_context"),
                                "db": db,
                                "session_id": request.session.sid
                            }
                        }
                    }
                    
                    # Create response with session cookie
                    response = Response(
                        json.dumps(response_data),
                        status=200,
                        content_type='application/json',
                        headers=headers
                    )
                    
                    # Ensure session cookie is set
                    response.set_cookie(
                        'session_id', 
                        request.session.sid,
                        max_age=http.SESSION_LIFETIME, 
                        httponly=True,
                        secure=request.httprequest.environ.get('HTTPS', False),
                        samesite='None' if request.httprequest.environ.get('HTTPS', False) else None
                    )
                    
                    logger.info(f"Login successful for user {login} (uid: {results.get('uid')})")
                    return response
                    
            except Exception as e:
                logger.error(f"General authentication error: {str(e)}")
                # Fall through to AccessDenied handler
                    
        except AccessDenied as e:
            logger.exception(f"Login failed - AccessDenied: {str(e)}")
            return Response(
                json.dumps({
                    "jsonrpc": "2.0",
                    "id": data.get('id', None) if 'data' in locals() else None,
                    "error": {
                        "code": 401,
                        "message": str(e)
                    }
                }),
                status=401,
                content_type='application/json',
                headers=headers
            )
        except Exception as e:
            logger.exception(f"Login error: {str(e)}")
            return Response(
                json.dumps({
                    "jsonrpc": "2.0", 
                    "id": data.get('id', None) if 'data' in locals() else None,
                    "error": {
                        "code": 500,
                        "message": str(e)
                    }
                }),
                status=500,
                content_type='application/json',
                headers=headers
            )
    
    def _try_direct_tenant_auth(self, tenant_db, login, password, business_id):
        """Try to authenticate directly to tenant database using HTTP request to tenant container"""
        import requests
        
        try:
            # Find tenant port from database
            partner = request.env['res.partner'].sudo().search([
                ('business_id', '=', business_id),
                ('company_type', '=', 'company')
            ], limit=1)
            
            if not partner:
                return {'success': False, 'message': 'Tenant not found'}
            
            # Use direct container authentication via HTTP request to tenant container
            tenant_url = f"http://localhost:{partner.port}/web/session/authenticate"
            
            logger.info(f"Trying direct container auth to {tenant_url} with user {login}")
            
            payload = {
                "jsonrpc": "2.0",
                "id": 123456,
                "params": {
                    "db": tenant_db,
                    "login": login,
                    "password": password
                }
            }
            
            # Make request to tenant container
            response = requests.post(
                tenant_url, 
                json=payload,
                headers={'Content-Type': 'application/json'},
                timeout=10
            )
            
            if response.status_code == 200:
                result = response.json()
                if result.get('result'):
                    logger.info(f"Direct container auth successful for {login} on {tenant_db}")
                    
                    # Extract session ID from cookies
                    session_id = None
                    if 'session_id' in response.cookies:
                        session_id = response.cookies['session_id']
                    
                    # Get user data from result
                    user_data = result.get('result')
                    
                    # Initialize roles variables
                    user_roles = []
                    user_role = None
                    
                    # If we have a session and user_id, fetch groups and tags
                    if session_id and user_data.get('uid'):
                        # Include session in cookies for subsequent requests
                        cookies = {'session_id': session_id}
                        
                        # 1. Fetch user groups
                        groups_url = f"http://localhost:{partner.port}/web/dataset/call_kw"
                        groups_payload = {
                            "jsonrpc": "2.0",
                            "id": 123457,
                            "params": {
                                "model": "res.users",
                                "method": "get_groups_for_external_api",
                                "args": [user_data.get('uid')],
                                "kwargs": {}
                            }
                        }
                        
                        # Make the request to get groups
                        groups_response = requests.post(
                            groups_url,
                            json=groups_payload,
                            headers={'Content-Type': 'application/json'},
                            cookies=cookies,
                            timeout=10
                        )
                        
                        # Extract roles from groups response
                        if groups_response.status_code == 200:
                            groups_result = groups_response.json()
                            if groups_result.get('result'):
                                # Process groups into roles
                                groups = groups_result.get('result', [])
                                for group in groups:
                                    role_name = group.get('name')
                                    if isinstance(role_name, dict) and 'en_US' in role_name:
                                        role_name = role_name['en_US']
                                    
                                    # Add to roles array
                                    user_roles.append({"role": role_name})
                                    
                                    # Determine primary role
                                    if role_name.lower() == 'admin' or role_name.lower() == 'administrator':
                                        user_role = "Admin"
                                    elif role_name.lower() == 'client' and not user_role:
                                        user_role = "Client"
                                    elif role_name.lower() == 'dispatcher' and not user_role:
                                        user_role = "Dispatcher"
                                    elif role_name.lower() == 'rider' and not user_role:
                                        user_role = "Rider"
                        
                        # 2. Fetch partner tags directly
                        if user_data.get('partner_id'):
                            partner_id = user_data.get('partner_id')
                            tags_url = f"http://localhost:{partner.port}/web/dataset/call_kw"
                            tags_payload = {
                                "jsonrpc": "2.0",
                                "id": 123458,
                                "params": {
                                    "model": "res.partner",
                                    "method": "read",
                                    "args": [[partner_id], ['category_id']],
                                    "kwargs": {}
                                }
                            }
                            
                            # Make the request to get partner category IDs
                            tags_response = requests.post(
                                tags_url,
                                json=tags_payload,
                                headers={'Content-Type': 'application/json'},
                                cookies=cookies,
                                timeout=10
                            )
                            
                            # Process tag names
                            if tags_response.status_code == 200:
                                tags_result = tags_response.json()
                                if tags_result.get('result') and tags_result['result']:
                                    category_ids = tags_result['result'][0].get('category_id', [])
                                    
                                    if category_ids:
                                        # Get tag names
                                        tag_names_payload = {
                                            "jsonrpc": "2.0",
                                            "id": 123459,
                                            "params": {
                                                "model": "res.partner.category",
                                                "method": "read",
                                                "args": [category_ids, ['name']],
                                                "kwargs": {}
                                            }
                                        }
                                        
                                        tag_names_response = requests.post(
                                            tags_url,
                                            json=tag_names_payload,
                                            headers={'Content-Type': 'application/json'},
                                            cookies=cookies,
                                            timeout=10
                                        )
                                        
                                        if tag_names_response.status_code == 200:
                                            tag_names_result = tag_names_response.json()
                                            if tag_names_result.get('result'):
                                                for tag in tag_names_result['result']:
                                                    tag_name = tag.get('name')
                                                    if isinstance(tag_name, dict) and 'en_US' in tag_name:
                                                        tag_name = tag_name['en_US']
                                                    
                                                    # Add to roles array
                                                    user_roles.append({"role": tag_name})
                                                    
                                                    # Determine primary role from tag name (prioritize tags)
                                                    if tag_name.lower() in ['admin', 'administrator']:
                                                        user_role = "Admin"
                                                    elif tag_name.lower() == 'client' and not user_role:
                                                        user_role = "Client" 
                                                    elif tag_name.lower() == 'dispatcher' and not user_role:
                                                        user_role = "Dispatcher"
                                                    elif tag_name.lower() == 'rider' and not user_role:
                                                        user_role = "Rider"
                    
                    # If no specific role found but user is admin, set admin role
                    if not user_role and user_data.get('is_admin'):
                        user_role = "Admin"
                    elif not user_role:
                        user_role = "User"  # Default fallback
                    
                    # If no roles found, create a minimal role array with the primary role
                    if not user_roles:
                        user_roles = [{"role": user_role}]
                    
                    # Return session information with roles
                    return {
                        'success': True,
                        'data': {
                            'code': 200,
                            'message': 'Logged in successfully',
                            'data': {
                                'id': user_data.get('uid'),
                                'uid': user_data.get('uid'),
                                'username': login,
                                'partner_id': user_data.get('partner_id'),
                                'name': user_data.get('name', ''),
                                'is_admin': user_data.get('is_admin', False),
                                'is_system': user_data.get('is_system', False),
                                'user_context': user_data.get('user_context', {}),
                                'db': tenant_db,
                                'session_id': session_id,
                                'role': user_role,  # Add primary role
                                'roles': user_roles  # Add roles array
                            }
                        }
                    }
                else:
                    logger.warning(f"Direct container auth failed for {login} on {tenant_db}: Invalid response")
                    return {'success': False, 'message': 'Invalid authentication response'}
            else:
                logger.warning(f"Direct container auth failed with status {response.status_code}")
                return {'success': False, 'message': f'Authentication request failed: {response.status_code}'}
                
        except Exception as e:
            logger.exception(f"Direct tenant authentication error: {str(e)}")
            return {'success': False, 'message': str(e)}

    @route('/api/v1/auth/logout', methods=['GET', 'OPTIONS'], type='http', auth="none", csrf=False, cors="*")
    def logout(self, **kw):
        headers = {
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Credentials': 'true',
            'Access-Control-Allow-Methods': 'GET, OPTIONS',
            'Access-Control-Allow-Headers': 'Content-Type, Authorization, X-Business-ID'
        }
        
        # Handle OPTIONS request for CORS
        if request.httprequest.method == 'OPTIONS':
            return Response(status=200, headers=headers)
            
        try:
            base_url = request.env['ir.config_parameter'].sudo().get_param('app_1_base_url')
            
            if not base_url:
                return Response(
                    json.dumps({
                        "jsonrpc": "2.0",
                        "error": {
                            "code": 500,
                            "message": "Base URL not found"
                        }
                    }),
                    status=500,
                    content_type='application/json',
                    headers=headers
                )
            
            # Get redirect URL
            redirect = base_url + "/authentication/signin"
            
            # Logout the user
            request.session.logout(keep_db=True)
            
            # Create response with redirect
            response = Response(
                json.dumps({
                    "jsonrpc": "2.0",
                    "result": {
                        "redirect": redirect
                    }
                }),
                status=200,
                content_type='application/json',
                headers=headers
            )
            
            # Clear session cookie
            response.delete_cookie('session_id')
            
            return response
            
        except Exception as e:
            logger.exception(f"Logout error: {str(e)}")
            return Response(
                json.dumps({
                    "jsonrpc": "2.0",
                    "error": {
                        "code": 500,
                        "message": str(e)
                    }
                }),
                status=500,
                content_type='application/json',
                headers=headers
            )
    
    @route('/api/v1/auth/forgot_password', methods=['POST', 'OPTIONS'], auth='public', type='http', csrf=False, cors="*")
    def generate_auth_token(self, **kw):
        headers = {
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Credentials': 'true',
            'Access-Control-Allow-Methods': 'POST, OPTIONS',
            'Access-Control-Allow-Headers': 'Content-Type, Authorization, X-Business-ID'
        }
        
        # Handle OPTIONS request for CORS
        if request.httprequest.method == 'OPTIONS':
            return Response(status=200, headers=headers)
                          
        try:
            data = json.loads(request.httprequest.data.decode('utf-8'))
            email = data.get("email")
            
            if not email:
                return Response(
                    json.dumps({
                        "jsonrpc": "2.0",
                        "error": {
                            "code": 400,
                            "message": "Email is required"
                        }
                    }),
                    status=400,
                    content_type='application/json',
                    headers=headers
                )
            
            auth_token = naidash_auth.generate_auth_token(email)
                
            return Response(
                json.dumps({
                    "jsonrpc": "2.0",
                    "result": auth_token
                }),
                status=200,
                content_type='application/json',
                headers=headers
            )
        except Exception as e:
            logger.exception(f"Forgot password error: {str(e)}")
            return Response(
                json.dumps({
                    "jsonrpc": "2.0",
                    "error": {
                        "code": 500,
                        "message": str(e)
                    }
                }),
                status=500,
                content_type='application/json',
                headers=headers
            )
            
    @route('/api/v1/auth/reset_password', methods=['POST', 'OPTIONS'], auth='public', type='http', csrf=False, cors="*")
    def reset_user_password(self, **kw):
        headers = {
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Credentials': 'true',
            'Access-Control-Allow-Methods': 'POST, OPTIONS',
            'Access-Control-Allow-Headers': 'Content-Type, Authorization, X-Business-ID'
        }
        
        # Handle OPTIONS request for CORS
        if request.httprequest.method == 'OPTIONS':
            return Response(status=200, headers=headers)
                          
        try:
            data = json.loads(request.httprequest.data.decode('utf-8'))
            
            if not data.get("token") or not data.get("password"):
                return Response(
                    json.dumps({
                        "jsonrpc": "2.0",
                        "error": {
                            "code": 400,
                            "message": "Token and password are required"
                        }
                    }),
                    status=400,
                    content_type='application/json',
                    headers=headers
                )
            
            reset_password = naidash_auth.reset_user_password(data)
            
            return Response(
                json.dumps({
                    "jsonrpc": "2.0",
                    "result": reset_password
                }),
                status=200,
                content_type='application/json',
                headers=headers
            )
        except Exception as e:
            logger.exception(f"Reset password error: {str(e)}")
            return Response(
                json.dumps({
                    "jsonrpc": "2.0",
                    "error": {
                        "code": 500,
                        "message": str(e)
                    }
                }),
                status=500,
                content_type='application/json',
                headers=headers
            )