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
                    
                    # Return session information
                    user_data = result.get('result')
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
                                'session_id': session_id
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