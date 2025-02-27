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
        
    
    
    @route('/api/v1/tenant/lookup/<string:business_id>', methods=['GET'], auth='public', type='http')
    def lookup_tenant(self, business_id):
        """Look up tenant details by business ID"""
        headers = [('Content-Type', 'application/json')]
        
        try:
            # Search for the tenant in res.partner with sudo() to bypass access rights
            tenant = request.env['res.partner'].sudo().search([
                ('business_id', '=', business_id),
                ('is_company', '=', True)
            ], limit=1)

            if not tenant:
                logger.warning(f'No tenant found for business_id: {business_id}')
                return request.make_response(json.dumps({
                    'code': 404,
                    'message': 'Tenant not found'
                }), headers)

            # Generate database name based on partner's record
            tenant_database = tenant.partner_database_name
            if not tenant_database:
                # Fallback to generating database name
                timestamp = tenant.create_date.strftime('%d%m%Y%H%M') if tenant.create_date else ''
                tenant_database = f'tdb_{business_id}_{timestamp}'

            # Get port from database configuration
            try:
                with open('/etc/nginx/conf.d/tenant_ports.conf', 'r') as f:
                    port_config = f.read()
                    # Parse the port from config using regex
                    import re
                    port_match = re.search(rf'{business_id}\s+(\d+);', port_config)
                    tenant_port = port_match.group(1) if port_match else None
            except Exception as e:
                logger.error(f'Error reading port configuration: {str(e)}')
                tenant_port = None

            # If port not found in config, generate dynamically starting from 8071
            if not tenant_port:
                base_port = 8071
                tenant_count = request.env['res.partner'].sudo().search_count([
                    ('is_company', '=', True),
                    ('create_date', '<=', tenant.create_date)
                ])
                tenant_port = base_port + (tenant_count - 1)

            tenant_details = {
                'tenant_id': tenant.partner_primary_id or business_id,
                'tenant_database': tenant_database,
                'tenant_url': f'http://localhost:{tenant_port}',
                'business_id': business_id,
                'name': tenant.name,
                'partner_id': tenant.id,
                'creation_date': tenant.create_date.strftime('%Y-%m-%d %H:%M:%S') if tenant.create_date else None,
                'company_type': tenant.company_type,
                'port': tenant_port,
                'is_active': bool(tenant.active)
            }

            # Add to port configuration if not exists
            try:
                if not port_match:
                    with open('/etc/nginx/conf.d/tenant_ports.conf', 'a') as f:
                        f.write(f'\n    {business_id}     {tenant_port};')
                    # Reload Nginx configuration
                    import subprocess
                    subprocess.run(['sudo', 'nginx', '-s', 'reload'])
            except Exception as e:
                logger.error(f'Error updating port configuration: {str(e)}')

            logger.info(f'Tenant lookup successful for {business_id}: {tenant_details}')
            
            return request.make_response(json.dumps({
                'code': 200,
                'data': tenant_details
            }), headers)

        except Exception as e:
            logger.error(f'Error during tenant lookup for {business_id}: {str(e)}')
            return request.make_response(json.dumps({
                'code': 500,
                'message': f'Internal server error: {str(e)}'
            }), headers)
        finally:
            try:
                request.env.cr.close()
            except Exception:
                pass
        
    @route('/v1/partner/<int:partner_id>', methods=['GET'], auth='user', type='http')
    def get_tenant_partner(self, partner_id):
        """Get tenant-specific partner details"""
        headers = [('Content-Type', 'application/json')]
        
        try:
            # Business ID is already available in the request headers
            business_id = request.httprequest.headers.get('X-Business-ID')
            logger.info(f"Fetching partner ID {partner_id} for tenant {business_id}")
            
            # Get partner details using standard method
            partner_details = request.env['res.partner'].get_the_partner(partner_id)
            status_code = partner_details.get("code")
            
            if status_code == 404:
                data = json.dumps({
                    "error": partner_details
                })
                return request.make_response(data, headers, status=status_code)
            else:
                data = json.dumps({
                    "result": partner_details
                })
                return request.make_response(data, headers, status=status_code)
                
        except Exception as e:
            logger.exception(f"Error retrieving tenant partner details: {str(e)}")
            data = json.dumps({
                "error": {
                    "code": 500,
                    "message": str(e)
                }
            })
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
            