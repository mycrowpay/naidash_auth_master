# -*- coding: utf-8 -*-
import json
import logging

from odoo import http
from odoo.http import request, route, SessionExpiredException
from odoo.service import security
from odoo.service.security import check_session

logger = logging.getLogger(__name__)

class NaidashPartner(http.Controller):
    @route('/api/v1/partner', methods=['POST'], auth='user', type='json')
    def create_partner(self, **kw):
        request.env.cr.execute('SET LOCAL statement_timeout = 600000')  # 5 minutes
        """Create the partner details
        """ 

        try:            
            request_data = json.loads(request.httprequest.data)                        
            partner_details = request.env['res.partner'].create_the_partner(request_data)
            return partner_details
        except Exception as e:
            logger.exception(f"The following error occurred while creating the partner details:\n\n{str(e)}")
            return {
                "code": 500,
                "message": str(e)
            }
            
    @route('/api/v1/partner/<int:partner_id>', methods=['PATCH'], auth='user', type='json')
    def edit_partner(self, partner_id, **kw):
        """Edit the partner details
        """ 
                
        try:
            request_data = json.loads(request.httprequest.data)
            partner_details = request.env['res.partner'].edit_the_partner(partner_id, request_data)
            return partner_details
        except TypeError as e:
            logger.error(f"This datatype error ocurred while modifying the partner details:\n\n{str(e)}")
            return {                
                "code": 422,
                "message": str(e)
            }        
        except Exception as e:
            logger.exception(f"This error occurred while modifying the partner details:\n\n{str(e)}")
            return {            
                "code": 500,
                "message": str(e)
            }
            
    @route('/api/v1/partner/<int:partner_id>', methods=['GET'], auth='user', type='http')
    def get_partner(self, partner_id):
        """Get the partner details
        """ 
                
        headers = [('Content-Type', 'application/json')]
        
        try:
            partner_details = request.env['res.partner'].get_the_partner(partner_id)
            status_code = partner_details.get("code")
            
            if status_code == 404:
                data = json.dumps(
                    {
                        "error": partner_details
                    }
                )

                return request.make_response(data, headers, status=status_code)                 
            else:
                data = json.dumps(
                    {
                        "result": partner_details
                    }
                )

                return request.make_response(data, headers, status=status_code)
        except Exception as e:
            logger.exception(f"The following error occurred while fetching the partner details:\n\n{str(e)}")
            data = json.dumps(
                {
                    "error": {
                        "code": 500,
                        "message": str(e)
                    }
                }
            )
            
            return request.make_response(data, headers, status=500)
        
    @route('/api/v1/partner', methods=['GET'], auth='user', type='http')
    def get_partners(self):
        """
        Returns all the partners
        """ 
        
        headers = [
            ('Content-Type', 'application/json')
        ]
                
        try:
            partner_details = request.env['res.partner'].get_all_the_partners()
            status_code = partner_details.get("code")
            
            if status_code == 404:
                data = json.dumps(
                    {
                        "error": partner_details
                    }
                )

                return request.make_response(data, headers, status=status_code)
            else:                
                data = json.dumps(
                    {
                        "result": partner_details
                    }
                )

                return request.make_response(data, headers, status=status_code)
        except Exception as e:
            logger.exception(f"The following error occurred while fetching the partners:\n\n{str(e)}")
            data = json.dumps(
                {
                    "error": {
                        "code": 500,
                        "message": str(e)}
                }
            )
            
            return request.make_response(data, headers, status=500)
            