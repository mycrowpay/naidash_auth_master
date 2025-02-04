import secrets
import string
import logging
import re


from odoo.http import request, content_disposition

logger = logging.getLogger(__name__)

class NaidashAuth:
    
    def generate_auth_token(self, email):
        """
        Generates an auth token and a password reset link.
        It also sends a password reset email to the user
        """
        
        try:
            response_data = dict(code=204, message="No content")

            if not email:
                response_data["code"] = 400
                response_data["message"] = "Email is required!"
                return response_data
                        
            reset_user_password = request.env['res.users'].sudo().reset_password(email)
            partner = request.env['res.partner'].sudo().search([('email', '=', email)], limit=1)                            
            
            if partner:
                response_data["code"] = 200
                response_data["message"] = "Please check your email for the password reset link"
                
            return response_data

        except Exception as e:
            logger.error(f"An error occurred while generating the authentication token:\n\n{str(e)}")
            raise e
        
    def reset_user_password(self, request_data):
        """Reset the user password"""
                          
        try:
            response_data = dict(code=204, message="No content")            
            token = request_data.get('token')
            confirm_password = request_data.get('confirm_password')
            new_password = request_data.get('new_password')
            
            # Find the partner corresponding to a token
            partner = request.env['res.partner'].sudo()._signup_retrieve_partner(token, check_validity=True)
                            
            if not partner:
                response_data["code"] = 400
                response_data["message"] = "Invalid token!"
                return response_data
            
            if not confirm_password or not new_password:
                response_data["code"] = 400
                response_data["message"] = "Password is required!"
                return response_data
            
            if confirm_password != new_password:
                response_data["code"] = 400
                response_data["message"] = "Passwords don't match!"
                return response_data
            
            # Find the user based on their email and update their new password
            user = request.env['res.users'].sudo().search([('login','=', partner.email)])
            if user:
                user.write({'password': new_password})
                
                response_data["code"] = 200
                response_data["message"] = "Password was reset successfully"
            
            # Invalidate/Remove the token
            partner.write(
                {
                    'signup_token': False,
                    'signup_type': False,
                    'signup_expiration': False,
                    'reset_password_url': False,
                    'is_email_verified': True
                }
            )
            
            return response_data
        except Exception as e:
            logger.error(f"An error occurred while resetting the password:\n\n{str(e)}")
            raise e                
            
    def auto_signup(self, partner_details, user_details):
        """Automatically sign up a user
        """
                           
        try:
            data = dict()
            response_data = dict(code=204, message="No content")
            partner = request.env['res.partner'].sudo().create_the_partner(partner_details)
            user = request.env['res.users'].sudo().create_the_user(user_details)
            
            if partner.get("data").get("id") and user.get("data").get("id"):
                data['partner_id'] = partner.get("data").get("id")
                data['user_id'] = user.get("data").get("id")
                
                response_data["code"] = 200
                response_data["message"] = "Account was created successfully"
                response_data["data"] = data
            
            return response_data
        except Exception as e:
            logger.error(f"An error occurred while signing up the user automatically:\n\n{str(e)}")
            raise e             