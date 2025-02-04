# -*- coding: utf-8 -*-
import json
import logging
import odoo

from odoo import http
from odoo.http import request, route, Response
from odoo.exceptions import AccessError, UserError, AccessDenied
from datetime import datetime, timedelta

from ..models.auth import NaidashAuth

logger = logging.getLogger(__name__)
naidash_auth = NaidashAuth()


class NaidashAuth(http.Controller):
    @route('/api/v1/auth/login', methods=['POST'], type='json', auth="none")
    def login(self, login, password):
        db = request.env.cr.dbname
        
        try:
            response_data = dict(code=401, message="Login failed!")
            
            if not http.db_filter([db]):
                raise AccessError("Database not found.")
            pre_uid = request.session.authenticate(db, login, password)        
                    
            if pre_uid != request.session.uid:
                # Crapy workaround for unupdatable Odoo Mobile App iOS (Thanks Apple :@) and Android
                # Correct behavior should be to raise AccessError("Renewing an expired session for user that has multi-factor-authentication is not supported. Please use /web/login instead.")
                return {"user_id": None}

            request.session.db = db
            registry = odoo.modules.registry.Registry(db)
            with registry.cursor() as cr:
                env = odoo.api.Environment(cr, request.session.uid, request.session.context)
                if not request.db and not request.session.is_explicit:
                    # request._save_session would not update the session_token
                    # as it lacks an environment, rotating the session myself
                    http.root.session_store.rotate(request.session, env)
                    
                    cookie_expiry_date = datetime.now() + timedelta(hours=2)
                    request.future_response.set_cookie(
                        'session_id', request.session.sid,
                        max_age=http.SESSION_LIFETIME, httponly=True,
                        expires=cookie_expiry_date
                    )
                
                user = request.env.user
                results = env['ir.http'].session_info()            
                response_data = dict()
                
                response_data["code"] = 200
                response_data["message"] = "Logged in successfully"
                response_data["data"] = dict(
                    id = results.get("uid"),
                    partner_id = results.get("partner_id"),
                )
                        
            return response_data
        except AccessDenied as e:
            logger.exception(f"The following `AccessDeniedError` occurred during login:\n\n{str(e)}")
            return {
                "code": 401,
                "message": str(e)
            }
        except Exception as e:
            logger.exception(f"The following error occurred during login:\n\n{str(e)}")
            return {
                "code": 500,
                "message": str(e)
            }
        
    @route('/api/v1/auth/logout', methods=['GET'], type='http', auth="none")
    def logout(self):
        headers = [('Content-Type', 'application/json')]
        
        try:
            base_url = request.env['ir.config_parameter'].sudo().get_param('app_1_base_url')
            
            if not base_url:
                data = json.dumps(
                    {
                        "error": {
                            "code": 500,
                            "message": "Base URL not found"
                        }
                    }
                )
                
                return request.make_response(data, headers, status=500)
            
            redirect = base_url + "/authentication/signin"
            request.session.logout(keep_db=True)
            # Redirects the user to a custom page after logging out.
            # Warning: Last parameter should be set to false
            return request.redirect(redirect, 303, False)
        except Exception as e:
            logger.exception(f"The following error occurred while logging out the user:\n\n{str(e)}")
            data = json.dumps(
                {
                    "error": {
                        "code": 500,
                        "message": str(e)
                    }
                }
            )
            
            return request.make_response(data, headers, status=500)
    
    @route('/api/v1/auth/forgot_password', methods=['POST'], auth='public', type='json')
    def generate_auth_token(self, **kw):
        """Generates an auth token and a password reset link.
        It also sends a password reset email to the user"""                          
        
        try:
            request_data = json.loads(request.httprequest.data)
            email = request_data.get("email")
            auth_token = naidash_auth.generate_auth_token(email)
                
            return auth_token
        except Exception as e:
            logger.exception(f"The following error occurred while generating the authentication token:\n\n{str(e)}")
            return {
                "code": 500,
                "message": str(e)
            }
            
    @route('/api/v1/auth/reset_password', methods=['POST'], auth='public', type='json')
    def reset_user_password(self, **kw):
        """Reset the user password"""
                          
        try:
            request_data = json.loads(request.httprequest.data)
            reset_password = naidash_auth.reset_user_password(request_data)
            
            return reset_password
        except Exception as e:
            logger.exception(f"The following error occurred while resetting the user's password:\n\n{str(e)}")
            return {
                "code": 500,
                "message": str(e)
            }
                    