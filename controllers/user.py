# -*- coding: utf-8 -*-
import requests
import json
import logging

from functools import wraps
from odoo import http
from odoo.http import request, route, SessionExpiredException, root, get_default_session
from odoo.service import security
from odoo.service.security import check_session
from odoo.exceptions import AccessDenied, AccessError, ValidationError, UserError


logger = logging.getLogger(__name__)

class NaidashUser(http.Controller):
    @route('/api/v1/user', methods=['POST'], auth='user', type='json')
    def create_user(self, **kw):
        """Create the user
        """ 

        try:            
            request_data = json.loads(request.httprequest.data)
            user_details = request.env['res.users'].create_the_user(request_data)
            return user_details
        except AccessError as e:
            logger.error(f"This AccessError ocurred while creating the user:\n\n{str(e)}")
            return {                
                "code": 403,
                "message": "Permission denied.Contact your administrator for assistance"
            }
        except Exception as e:
            logger.exception(f"The following error occurred while creating the user details:\n\n{str(e)}")
            return {
                "code": 500,
                "message": str(e)
            }
            
    @route('/api/v1/user/<int:user_id>', methods=['PATCH'], auth='user', type='json')
    def edit_user(self, user_id, **kw):
        """Edit the user details
        """        
        
        try:            
            request_data = json.loads(request.httprequest.data)
            user_details = request.env['res.users'].edit_the_user(user_id, request_data)
            return user_details
        except AccessError as e:
            logger.error(f"This AccessError ocurred while modifying the user details:\n\n{str(e)}")
            return {                
                "code": 403,
                "message": "Permission denied.Contact your administrator for assistance"
            }
        except TypeError as e:
            logger.error(f"This datatype error ocurred while modifying the user details:\n\n{str(e)}")
            return {                
                "code": 422,
                "message": str(e)
            }
        except Exception as e:   
            logger.exception(f"This error occurred while modifying the user details:\n\n{str(e)}")
            return {            
                "code": 500,
                "message": str(e)
            }
            
    @route('/api/v1/user/<int:user_id>', methods=['GET'], auth='user', type='http')
    def get_user(self, user_id):
        """Get the user details
        """ 
                
        headers = [('Content-Type', 'application/json')]
                
        try:
            user_details = request.env['res.users'].get_the_user(user_id)
            status_code = user_details.get("code")
            
            if status_code == 404:
                data = json.dumps(
                    {
                        "error": user_details
                    }
                )

                return request.make_response(data, headers, status=status_code)                 
            else:
                data = json.dumps(
                    {
                        "result": user_details
                    }
                )

                return request.make_response(data, headers, status=status_code)
        except AccessError as e:
            logger.error(f"This AccessError ocurred while fetching the user details:\n\n{str(e)}")
            data = json.dumps(
                {
                    "error": {
                        "code": 403,
                        "message": "Permission denied.Contact your administrator for assistance"
                    }
                }
            )
            
            return request.make_response(data, headers, status=403)
        except Exception as e:
            logger.exception(f"This error occurred while fetching the user details:\n\n{str(e)}")
            data = json.dumps(
                {
                    "error": {
                        "code": 500,
                        "message": str(e)
                    }
                }
            )
            
            return request.make_response(data, headers, status=500)
        
    @route('/api/v1/user', methods=['GET'], auth='user', type='http')
    def get_users(self):
        """
        Returns all the users
        """ 
        
        headers = [
            ('Content-Type', 'application/json')
        ]
                
        try:
            user_details = request.env['res.users'].get_all_the_users()
            status_code = user_details.get("code")
            
            if status_code == 404:
                data = json.dumps(
                    {
                        "error": user_details
                    }
                )

                return request.make_response(data, headers, status=status_code)
            else:                
                data = json.dumps(
                    {
                        "result": user_details
                    }
                )

                return request.make_response(data, headers, status=status_code)
        except AccessError as e:
            logger.error(f"This AccessError ocurred while fetching the user details:\n\n{str(e)}")
            data = json.dumps(
                {
                    "error": {
                        "code": 403,
                        "message": "Permission denied.Contact your administrator for assistance"
                    }
                }
            )
            
            return request.make_response(data, headers, status=403)
        except Exception as e:
            logger.exception(f"The following error occurred while fetching the users:\n\n{str(e)}")
            data = json.dumps(
                {
                    "error": {
                        "code": 500,
                        "message": str(e)}
                }
            )
            
            return request.make_response(data, headers, status=500)