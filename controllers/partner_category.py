# -*- coding: utf-8 -*-
import json
import logging

from odoo import http
from odoo.http import request, route, SessionExpiredException
from odoo.service import security
from odoo.service.security import check_session

logger = logging.getLogger(__name__)

class NaidashPartnerCategory(http.Controller):
    @route('/api/v1/partner_category', methods=['POST'], auth='user', type='json')
    def create_partner_category(self, **kw):
        """Create the partner category
        """ 

        try:            
            request_data = json.loads(request.httprequest.data)                        
            partner_category = request.env['res.partner.category'].create_the_partner_category(request_data)
            return partner_category
        except Exception as e:
            logger.exception(f"The following error occurred while creating the partner category:\n\n{str(e)}")
            return {
                "code": 500,
                "message": str(e)
            }
            
    @route('/api/v1/partner_category/<int:category_id>', methods=['PATCH'], auth='user', type='json')
    def edit_partner_category(self, category_id, **kw):
        """Edit the partner category
        """ 
                
        try:
            request_data = json.loads(request.httprequest.data)
            partner_category = request.env['res.partner.category'].edit_the_partner_category(category_id, request_data)
            return partner_category
        except TypeError as e:
            logger.error(f"This datatype error ocurred while modifying the partner category:\n\n{str(e)}")
            return {                
                "code": 422,
                "message": str(e)
            }        
        except Exception as e:
            logger.exception(f"This error occurred while modifying the partner category:\n\n{str(e)}")
            return {            
                "code": 500,
                "message": str(e)
            }
            
    @route('/api/v1/partner_category/<int:category_id>', methods=['GET'], auth='user', type='http')
    def get_partner_category(self, category_id):
        """Get the partner category
        """ 
                
        headers = [('Content-Type', 'application/json')]
        
        try:
            partner_category = request.env['res.partner.category'].get_the_partner_category(category_id)
            status_code = partner_category.get("code")
            
            if status_code == 404:
                data = json.dumps(
                    {
                        "error": partner_category
                    }
                )

                return request.make_response(data, headers, status=status_code)                 
            else:
                data = json.dumps(
                    {
                        "result": partner_category
                    }
                )

                return request.make_response(data, headers, status=status_code)
        except Exception as e:
            logger.exception(f"The following error occurred while fetching the partner category:\n\n{str(e)}")
            data = json.dumps(
                {
                    "error": {
                        "code": 500,
                        "message": str(e)
                    }
                }
            )
            
            return request.make_response(data, headers, status=500)
        
    @route('/api/v1/partner_category', methods=['GET'], auth='user', type='http')
    def get_partner_categories(self):
        """
        Returns all the partner categories
        """ 
        
        headers = [
            ('Content-Type', 'application/json')
        ]
                
        try:
            partner_categories = request.env['res.partner.category'].get_all_the_partner_categories()
            status_code = partner_categories.get("code")
            
            if status_code == 404:
                data = json.dumps(
                    {
                        "error": partner_categories
                    }
                )

                return request.make_response(data, headers, status=status_code)
            else:                
                data = json.dumps(
                    {
                        "result": partner_categories
                    }
                )

                return request.make_response(data, headers, status=status_code)
        except Exception as e:
            logger.exception(f"The following error occurred while fetching the partner categories:\n\n{str(e)}")
            data = json.dumps(
                {
                    "error": {
                        "code": 500,
                        "message": str(e)}
                }
            )
            
            return request.make_response(data, headers, status=500)
            