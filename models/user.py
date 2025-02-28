import contextlib
import logging
import json
import base64

from odoo import models, fields, api, _, tools, SUPERUSER_ID, Command
from odoo.exceptions import AccessDenied, AccessError, ValidationError, UserError
from odoo.addons.base.models.ir_mail_server import MailDeliveryException
from odoo.addons.auth_signup.models.res_partner import now
from odoo.tools import email_normalize
from odoo.http import request, SessionExpiredException

logger = logging.getLogger(__name__)


class NaidashUser(models.Model):
    _inherit = "res.users"
    
    
    def create_the_user(self, request_data):
        """Create a user"""
                
        try:
            data = dict()
            response_data = dict(code=400, message="Bad request!")
            partner_id = request_data.get("partner_id")
            is_customer = request_data.get("is_customer")
            
            if not partner_id:
                response_data["code"] = 400
                response_data["message"] = "Partner ID is required!"
                return response_data
            
            if partner_id and request_data:
                partner = self.env['res.partner'].sudo().search([('id','=',int(partner_id))])
                
                if not partner:
                    response_data["code"] = 404
                    response_data["message"] = "Partner not found!"
                    return response_data
                
                if not partner.email:
                    partner_name = partner.name
                    username = partner_name.partition(" ")[0] if partner.company_type == "person" else partner_name
                                        
                    response_data["code"] = 404
                    response_data["message"] = f"An email is required to complete the account setup for `{username}`"
                    return response_data
                
                user_account = self.env['res.users'].search(
                    [
                        ('login','=', partner.email),
                        '|', ('active','=', True), ('active','=', False)
                    ], limit=1
                )
                
                if user_account:
                    response_data["code"] = 409
                    response_data["message"] = "Account already exists!"
                    return response_data
                
                user_details = {
                    "lang": "en_US",
                    "tz": "Africa/Nairobi",
                    "partner_id": partner.id,
                    "login": partner.email,
                    "company_id": partner.company_id.id if partner.company_id else False,
                    'company_ids': [(6, 0, [partner.company_id.id])] if partner.company_id else []
                }
            
                if is_customer == True or is_customer == False:
                    internal_user = self.env.ref('base.group_user', False)
                    portal_user = self.env.ref('base.group_portal', False)
                    
                    user_details["groups_id"] = [portal_user.id] if is_customer else [internal_user.id]
                                    
                user = self.env['res.users'].create(user_details)
                
                if user:
                    data['id'] = user.id
                    response_data["code"] = 201
                    response_data["message"] = "User created successfully"
                    response_data["data"] = data
                
            return response_data
        except AccessDenied as e:
            logger.error(f"AccessDenied error ocurred while creating the user:\n\n{str(e)}")
            raise e
        except AccessError as e:
            logger.error(f"AccessError ocurred while creating the user:\n\n{str(e)}")
            raise e        
        except Exception as e:
            logger.error(f"An error occurred while creating the user:\n\n{str(e)}")                        
            raise e
        
    def edit_the_user(self, user_id, request_data):
        """Edit the user details
        """             
                        
        try:
            response_data = dict(code=204, message="Nothing to update")
            partner_id = request_data.get("partner_id")
            is_customer = request_data.get("is_customer")
            is_active = request_data.get("active")
            
            if not user_id:
                response_data["code"] = 400
                response_data["message"] = "User ID is required!"
                return response_data
            
            if not partner_id:
                response_data["code"] = 400
                response_data["message"] = "Partner ID is required!"
                return response_data
                        
            user = self.env['res.users'].search(
                [
                    ('id','=', int(user_id)), 
                    '|', ('active','=', True), ('active','=', False)
                ]
            )
            
            if user:
                user_details = dict()

                if is_active == True or is_active == False:
                    user_details["active"] = is_active
                    
                if is_customer == True or is_customer == False:
                    internal_user = self.env.ref('base.group_user', False)
                    portal_user = self.env.ref('base.group_portal', False)

                    if internal_user and portal_user:
                        # if Customer and they happen to have an internal user licence, change it to a portal user
                        if is_customer == True and (user.id in internal_user.users.ids or user.id not in portal_user.users.ids):
                            user_details["groups_id"] = [
                                Command.set(user.groups_id.ids),
                                Command.unlink(internal_user.id),
                                Command.link(portal_user.id)
                            ]            
                        elif is_customer == False and (user.id in portal_user.users.ids or user.id not in internal_user.users.ids):
                            user_details["groups_id"] = [
                                Command.set(user.groups_id.ids),
                                Command.unlink(portal_user.id),
                                Command.link(internal_user.id)
                            ]
                                          
                if partner_id:                    
                    if partner_id == user.partner_id.id:
                        user_details["partner_id"] = user.partner_id.id
                        user_details["login"] = user.partner_id.email
                        user_details["company_id"] = user.partner_id.company_id.id if user.partner_id.company_id else False
                        user_details["company_ids"] = [user.partner_id.company_id.id] if user.partner_id.company_id else []
                    
                # Update user details
                if user_details:
                    user.write(user_details)
                    response_data["code"] = 200
                    response_data["message"] = "Updated successfully"
            else:
                response_data["code"] = 404
                response_data["message"] = "User not found!"                    
            
            return response_data
        except AccessDenied as e:
            logger.error(f"AccessDenied error ocurred while modifying the user details:\n\n{str(e)}")
            raise e
        except AccessError as e:
            logger.error(f"AccessError ocurred while modifying the user details:\n\n{str(e)}")
            raise e
        except TypeError as e:
            logger.error(f"Datatype error ocurred while modifying the user details:\n\n{str(e)}")
            raise e
        except Exception as e:
            logger.error(f"An error ocurred while modifying the user details:\n\n{str(e)}")
            raise e
        
    def get_the_user(self, user_id):
        """Get the user details
        """        
        
        try:
            data = dict()
            response_data = dict(code=404, message="User not found!")
            
            if not user_id:
                response_data["code"] = 400
                response_data["message"] = "User ID is required!"
                return response_data
            
            user = self.env['res.users'].search(
                [
                    ('id','=', int(user_id)), 
                    '|', ('active','=', True), ('active','=', False)
                ]
            )
            
            if user:
                data["id"] = user.id
                data["name"] = user.name
                data["email"] = user.login
                data["timezone"] = user.tz or ""
                data["is_portal"] = user._is_portal()
                data["is_internal"] = user._is_internal()
                data["is_admin"] = user._is_admin()
                data["active"] = user.active
                data["partner"] = {"id": user.partner_id.id, "name": user.partner_id.name} if user.partner_id else {}
                data["company"] = {"id": user.company_id.id, "name": user.company_id.name} if user.company_id else {}
                data["company_ids"] = [{"id": company.id, "name": company.name} for company in user.company_ids] if user.company_ids else []
                # data["group_ids"] = [{"id": group.id, "name": group.name} for group in user.groups_id] if user.groups_id else []
                # data["is_system"] = user._is_system()
                
                response_data["code"] = 200
                response_data["message"] = "Success"
                response_data["data"] = data
            
            return response_data
        except AccessDenied as e:
            logger.error(f"AccessDenied error ocurred while fetching the user details:\n\n{str(e)}")
            raise e
        except AccessError as e:
            logger.error(f"AccessError error ocurred while fetching the user details:\n\n{str(e)}")
            raise e        
        except Exception as e:
            logger.error(f"The following error ocurred while fetching the user details:\n\n{str(e)}")
            raise e
        
    def get_all_the_users(self):
        """Get all the users
        """        
        
        try:
            all_users = []
            response_data = dict(code=404, message="User not found!")
            users = self.env['res.users'].search(
                [
                    '|',
                    ('active','=', True),
                    ('active','=', False)
                ]
            )
            
            if users:
                for user in users:
                    data = dict()
                    data["id"] = user.id
                    data["name"] = user.name
                    data["email"] = user.login
                    data["timezone"] = user.tz or ""
                    data["is_portal"] = user._is_portal()
                    data["is_internal"] = user._is_internal()
                    data["is_admin"] = user._is_admin()
                    data["active"] = user.active
                    data["partner"] = {"id": user.partner_id.id, "name": user.partner_id.name} if user.partner_id else {}
                    data["company"] = {"id": user.company_id.id, "name": user.company_id.name} if user.company_id else {}
                    data["company_ids"] = [{"id": company.id, "name": company.name} for company in user.company_ids] if user.company_ids else []
                    # data["group_ids"] = [{"id": group.id, "name": group.name} for group in user.groups_id] if user.groups_id else []
                    # data["is_system"] = user._is_system()                    
                    
                    all_users.append(data)
                
                response_data["code"] = 200
                response_data["message"] = "Success"
                response_data["data"] = all_users
            
            return response_data
        except AccessDenied as e:
            logger.error(f"AccessDenied error ocurred while fetching the users:\n\n{str(e)}")
            raise e
        except AccessError as e:
            logger.error(f"AccessError ocurred while fetching the users:\n\n{str(e)}")
            raise e        
        except Exception as e:
            logger.error(f"The following error ocurred while fetching the users:\n\n{str(e)}")
            raise e        
        
    def reset_password(self, login):
        """ retrieve the user corresponding to login (login or email),
            and reset their password
        """
        users = self.search(self._get_login_domain(login))
        if not users:
            users = self.search(self._get_email_domain(login))
        if not users:
            raise Exception(_('Ooops! The email you provided does not exist'))
        if len(users) > 1:
            raise Exception(_('Ooops! Multiple accounts found for this email'))
        return users.action_reset_password()

    def action_reset_password(self):
        try:
            return self._action_reset_password()
        except MailDeliveryException as mde:
            if len(mde.args) == 2 and isinstance(mde.args[1], ConnectionRefusedError):
                raise UserError(_("Could not contact the mail server, please check your outgoing email server configuration")) from mde
            else:
                raise UserError(_("There was an error when trying to deliver your Email, please check your configuration")) from mde
                
    def _action_reset_password(self):
        """ create signup token for each user, and send their signup url by email """
        if self.env.context.get('install_mode') or self.env.context.get('import_file'):
            return
        if self.filtered(lambda user: not user.active):
            raise UserError(_("You cannot perform this action on an archived user."))
        # prepare reset password signup
        create_mode = bool(self.env.context.get('create_user'))

        # no time limit for initial invitation, only for reset password
        expiration = False if create_mode else now(days=+1)

        self.mapped('partner_id').signup_prepare(signup_type="reset", expiration=expiration)

        base_url = self.env['ir.config_parameter'].sudo().get_param('app_1_base_url')
        
        for user in self:
            if not user.email:
                raise UserError(_("Cannot send email: user %s has no email address.", user.name))            
            
            if base_url and user.partner_id.signup_valid: 
                # Generate the password reset link
                generate_password_reset_link = base_url + "/authentication/reset-password?token=" + user.partner_id.signup_token
                user.partner_id.write({'reset_password_url': generate_password_reset_link})             
            
                # Fetch email template
                email_template = self.env['mail.template'].sudo().search(
                    [
                        ('name', '=', 'Password Reset Notification(Customized)')
                    ], limit=1
                )

                if email_template:
                    # Send the email notification
                    email_template.sudo().send_mail(user.id, force_send=True, raise_exception=True)                
                    logger.info(f"A password reset email has been sent to {user.email}")                    

    def get_app_1_base_url(self):
        """ Returns the base URL for app no. 1
        """
        if len(self) > 1:
            raise ValueError("Expected singleton or no record: %s" % self)
        
        base_url = self.env['ir.config_parameter'].sudo().get_param('app_1_base_url')    
        return base_url
    
    
    
    
    @api.model
    def get_groups_for_external_api(self, user_id=None):
        """Get user groups and partner tags for external API consumption"""
        if user_id:
            user = self.browse(user_id)
        else:
            user = self
            
        result = []
        
        # Get all groups the user belongs to
        user_groups = user.groups_id
        
        # Format groups for API response
        for group in user_groups:
            role_name = group.name
            # Handle translated names if needed
            if isinstance(role_name, dict) and 'en_US' in role_name:
                role_name = role_name['en_US']
                
            result.append({
                'id': group.id,
                'name': role_name,
                'category_id': group.category_id.id if group.category_id else False,
                'category_name': group.category_id.name if group.category_id else False,
                'source': 'group'
            })
        
        # Add partner tags as roles
        if user.partner_id and user.partner_id.category_id:
            for tag in user.partner_id.category_id:
                tag_name = tag.name
                if isinstance(tag_name, dict) and 'en_US' in tag_name:
                    tag_name = tag_name['en_US']
                    
                result.append({
                    'id': tag.id,
                    'name': tag_name,
                    'category_id': False,
                    'category_name': 'Partner Tags',
                    'source': 'tag'
                })
        
        return result
    