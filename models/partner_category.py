import logging
import requests

from datetime import datetime
from odoo import models, _, fields, api
from odoo.http import request, SessionExpiredException
from odoo.exceptions import AccessDenied, AccessError, ValidationError, UserError

logger = logging.getLogger(__name__)

class NaidashPartnerCategory(models.Model):
    _inherit = "res.partner.category"
    
        
    def create_the_partner_category(self, request_data):
        """Create a partner category
        """ 
        
        try:
            data = dict()
            response_data = dict()
            
            name = request_data.get("name")
            parent_id = request_data.get("parent_id")
            
            if not name:
                response_data["code"] = 400
                response_data["message"] = "Name is required!"
                return response_data
            
            partner_tag = dict(name=(name.strip()).title())
            
            if parent_id:
                parent_category = self.env['res.partner.category'].browse(parent_id)
                
                if parent_category:
                    partner_tag["parent_id"] = parent_category.id
                else:
                    response_data["code"] = 404
                    response_data["message"] = "Partner category not found!"
                    return response_data
                
            partner_category = self.env['res.partner.category'].create(partner_tag)

            if partner_category:
                data['id'] = partner_category.id
                response_data["code"] = 201
                response_data["message"] = "Created successfully"
                response_data["data"] = data
            
            return response_data
        except AccessDenied as e:
            logger.error(f"AccessDenied error ocurred while creating the partner category:\n\n{str(e)}")
            raise e
        except AccessError as e:
            logger.error(f"AccessError ocurred while creating the partner category:\n\n{str(e)}")
            raise e
        except Exception as e:
            logger.error(f"An error ocurred while creating the partner category:\n\n{str(e)}")
            raise e
        
    def edit_the_partner_category(self, category_id, request_data):
        """Edit the partner category
        """ 
                
        try:
            response_data = dict()
            
            if not category_id:
                response_data["code"] = 400
                response_data["message"] = "Category ID is required!"
                return response_data
                        
            partner_category = self.env['res.partner.category'].search(
                [
                    ('id','=', int(category_id)), 
                    '|', ('active','=', True), ('active','=', False)
                ]
            )
            
            if partner_category:
                partner_category_details = dict()
                
                if request_data.get("name"):
                    name = (request_data.get("name")).strip()
                    name = name.title()
                    partner_category_details["name"] = name
                    
                if request_data.get("parent_id"):
                    partner_category_details["parent_id"] = int(request_data.get("parent_id"))
                    
                if request_data.get("active") == True or request_data.get("active") == False:
                    partner_category_details["active"] = request_data.get("active")
                    
                # Update partner category
                if partner_category_details:
                    partner_category.write(partner_category_details)
                    response_data["code"] = 200
                    response_data["message"] = "Updated successfully"
                else:
                    response_data["code"] = 204
                    response_data["message"] = "Nothing to update"
            else:
                response_data["code"] = 404
                response_data["message"] = "Partner category not found!"                    
            
            return response_data
        except AccessDenied as e:
            logger.error(f"AccessDenied error ocurred while modifying the partner category:\n\n{str(e)}")
            raise e
        except AccessError as e:
            logger.error(f"AccessError ocurred while modifying the partner category:\n\n{str(e)}")
            raise e        
        except TypeError as e:
            logger.error(f"Datatype error ocurred while modifying the partner category:\n\n{str(e)}")
            raise e
        except Exception as e:
            logger.error(f"An error ocurred while modifying the partner category:\n\n{str(e)}")
            raise e
        
    def get_the_partner_category(self, category_id):
        """Get the partner category details
        """        
        
        try:
            data = dict()
            response_data = dict()
            
            if not category_id:
                response_data["code"] = 400
                response_data["message"] = "Category ID is required!"
                return response_data
            
            partner_category = self.env['res.partner.category'].search(
                [
                    ('id','=', int(category_id)), 
                    '|', ('active','=', True), ('active','=', False)
                ]
            )
            
            if partner_category:
                data["id"] = partner_category.id
                data["name"] = partner_category.name
                data["active"] = partner_category.active
                data["parent"] = {"id": partner_category.parent_id.id, "name": partner_category.parent_id.name} if partner_category.parent_id else {}
                
                response_data["code"] = 200
                response_data["message"] = "Success"
                response_data["data"] = data
            else:
                response_data["code"] = 404
                response_data["message"] = "Partner category not found!"
            
            return response_data
        except AccessDenied as e:
            logger.error(f"AccessDenied error ocurred while fetching the partner category details:\n\n{str(e)}")
            raise e
        except AccessError as e:
            logger.error(f"AccessError ocurred while fetching the partner category details:\n\n{str(e)}")
            raise e
        except Exception as e:
            logger.error(f"The following error ocurred while fetching the partner category details:\n\n{str(e)}")
            raise e
        
    def get_all_the_partner_categories(self):
        """Get all the partner categories
        """        
        
        try:
            response_data = dict()
            all_partner_categories = []
            partner_categories = self.env['res.partner.category'].search(
                [
                    '|',
                    ('active','=', True),
                    ('active','=', False)
                ]
            )
            
            if partner_categories:
                for partner_category in partner_categories:
                    data = dict()
                    data["id"] = partner_category.id
                    data["name"] = partner_category.name
                    data["active"] = partner_category.active
                    data["parent"] = {"id": partner_category.parent_id.id, "name": partner_category.parent_id.name} if partner_category.parent_id else {}
                    
                    all_partner_categories.append(data)
                
                response_data["code"] = 200
                response_data["message"] = "Success"
                response_data["data"] = all_partner_categories
            else:
                response_data["code"] = 404
                response_data["message"] = "Partner categories not found!"
            
            return response_data
        except AccessDenied as e:
            logger.error(f"AccessDenied error ocurred while fetching the partner categories:\n\n{str(e)}")
            raise e
        except AccessError as e:
            logger.error(f"AccessError ocurred while fetching the partner categories:\n\n{str(e)}")
            raise e
        except Exception as e:
            logger.error(f"The following error ocurred while fetching the partner categories:\n\n{str(e)}")
            raise e