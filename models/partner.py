import logging
import requests
import base64
import random
import string
import subprocess
import os

from datetime import datetime
from odoo import models, _, fields, api
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
        
    def create_the_partner(self, request_data):
        """Create a partner
        """ 
        
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
            country_id = request_data.get("country_id")
            state_id = request_data.get("county_id")#county/state
            street = request_data.get("street_address")#address
            # pick_up_address
            # shipping_address
            
            if account_type != "individual" and account_type != "company":
                response_data["code"] = 400
                response_data["message"] = "Account type is required!"
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
            
            if isinstance(tag_ids, list) == False:
                response_data["code"] = 422
                response_data["message"] = "Expected a list of integer(s) in `tag_ids`"
                return response_data
            
            if email:
                query_params.append('|')
                query_params.append(("email", "ilike", email))
                
            country = self.env["res.country"].search([('code','=', 'KE')], order='id asc', limit=1)
                
            if phone_number.startswith("01") or phone_number.startswith("07"):
                country_code = str(country.phone_code)
                phone_number = phone_number.replace('01', country_code, 1).replace('07', country_code, 1)
                phone_number = phone_number.strip()
                
            query_params.append(('phone','=', phone_number))
            query_params.append(('mobile','=', phone_number))            
            
            admin = self.env["res.users"].search([], order='id asc', limit=1)
            partner_account = self.env["res.partner"].search(query_params, order='id asc', limit=1)
            
            if partner_account:
                response_data["code"] = 409
                response_data["message"] = "Account already exists!"
                return response_data

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
            
            if tag_ids:
                tags = self.env['res.partner.category'].browse(tag_ids)
                
                if tags:
                    partner_details["category_id"] = [tag.id for tag in tags]
                else:
                    response_data["code"] = 404
                    response_data["message"] = "Tag not found!"
                    return response_data
                
            partner = self.env['res.partner'].create(partner_details)

            if partner:
                tenant_password = "".join(random.choices(string.ascii_uppercase + string.digits, k=10))
                if partner.company_type == "company":
                    tenant_id = "TID_" + "".join(random.choices(string.ascii_uppercase + string.digits, k=10))
                    tenant_database = "TDB_" + ''.join(random.choices(string.ascii_uppercase + string.digits, k=10))
                    partner.sudo().write(
                        {
                            "partner_database_name": tenant_database,
                            "partner_primary_id": tenant_id, # username
                            "partner_secondary_id": tenant_password, # password
                        }
                    )
                    
                    args = [tenant_database, tenant_id, tenant_password]
                                        
                    directory = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../..'))
                    script = 'create_tenant.sh'
                    subprocess.call([f"{directory}/{script}"] + args, cwd=directory)
                                    
                data['id'] = partner.id
                response_data["code"] = 201
                response_data["message"] = "Partner created successfully"
                response_data["data"] = data
            
            return response_data
        except AccessDenied as e:
            logger.error(f"AccessDenied error ocurred while creating the partner:\n\n{str(e)}")
            raise e
        except AccessError as e:
            logger.error(f"AccessError ocurred while creating the partner:\n\n{str(e)}")
            raise e
        except Exception as e:
            logger.error(f"An error ocurred while creating the partner:\n\n{str(e)}")
            raise e
        
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