
from odoo import models, fields, api
import json
from odoo.exceptions import ValidationError
import logging
import base64
import io
from datetime import datetime


from odoo.http import request, Response
import requests

_logger = logging.getLogger(__name__)


class PaymentOrder(models.Model):
    _inherit = 'account.move'

    def action_initiate_wave_payment(self):
        """Action pour initier un paiement Wave"""
        try:
            # Récupérer les informations nécessaires
            transaction_id = f"TXN-{self.id}-{fields.Datetime.now().strftime('%Y%m%d%H%M%S')}"
            account_move = self.id
            partner_id = self.partner_id.id
            phone_number = self.partner_id.phone or ''
            amount = self.amount_total
            description = f"Paiement pour la commande {self.name}"
            currency = self.currency_id.name
            reference = self.name
            success_url = f"https://dev.ccbmshop.com/wave-paiement?transaction={transaction_id}"

            # Appeler la fonction pour initier le paiement Wave
            response = self._initiate_wave_payment(transaction_id, account_move, partner_id, phone_number, amount, description, currency, reference, success_url)

            if response.get('success'):
                payment_url = response.get('payment_url')
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': 'Paiement Wave initié',
                        'message': f'Le paiement Wave a été initié avec succès. Le lien de paiement sera ouvert dans un nouvel onglet.',
                        'type': 'info',
                        'next': {
                            'type': 'ir.actions.act_url',
                            'url': payment_url,
                            'target': 'new',
                        }
                    }
                }
            else:
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': 'Erreur',
                        'message': f'Erreur lors de l\'initiation du paiement Wave: {response.get("message")}',
                        'type': 'danger',
                    }
                }
        except Exception as e:
            _logger.error(f"Erreur lors de l'initiation du paiement Wave: {str(e)}")
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Erreur',
                    'message': f'Erreur lors de l\'initiation du paiement Wave: {str(e)}',
                    'type': 'danger',
                }
            }

    def _initiate_wave_payment(self, transaction_id, account_move_id, partner_id, phone_number, amount, description, currency, reference, success_url):
        """Initier un paiement Wave avec checkout sessions"""
        try:
            # Validation des paramètres requis
            data = {
                'transaction_id': transaction_id,
                'account_move_id': account_move_id,
                'partner_id': partner_id,
                'phoneNumber': phone_number,
                'amount': amount,
                'description': description,
                'currency': currency,
                'reference': reference,
                'success_url': success_url
            }

            # Réccupérer la configuration Wave active
            config = self.env['wave.config'].sudo().search([('is_active', '=', True)], limit=1)
            if not config:
                return {'error': 'Wave configuration not found', 'success': False}

            # Vérifier l'existence de l'order et du partner
            account_move = self.env['sale.order'].sudo().browse(int(account_move_id)) if account_move_id else None
            partner = self.env['res.partner'].sudo().browse(int(partner_id)) if partner_id else None


            if not account_move:
                return {'message': "La commande n'existe pas", 'success': False}
            if not partner:
                return {'message': "Le partenaire n'existe pas", 'success': False}

            # Vérifier si la transaction Wave existe déjà
            existing_tx = self.env['wave.transaction'].sudo().search([('transaction_id', '=', transaction_id)], limit=1)
            if existing_tx:
                return {
                    'success': True,
                    'transaction_id': existing_tx.transaction_id,
                    'wave_id': existing_tx.wave_id,
                    'session_id': existing_tx.wave_id,
                    'payment_url': existing_tx.payment_link_url,
                    'status': existing_tx.status or 'pending',
                    'account_move_id': existing_tx.account_move_id.id,
                    'partner_id': existing_tx.partner_id.id,
                    'reference': existing_tx.reference,
                    'success_url': success_url,
                    'existe': True
                }

            payload = {
                "amount": amount,
                "currency": currency,
                "success_url": success_url,
                "error_url": config.callback_url
            }

            headers = {
                "Authorization": f"Bearer {config.api_key}",
                "Content-Type": "application/json",
            }

            # Appel à l'API Wave checkout sessions
            response = requests.post(
                "https://api.wave.com/v1/checkout/sessions",
                json=payload,
                headers=headers,
                timeout=30
            )

            if response.status_code in [200, 201]:
                data = response.json()
                _logger.info(f"Wave checkout sessions response: {data}")

                # Créer la transaction dans Odoo
                wave_transaction = self.env['wave.transaction'].sudo().create({
                    'wave_id': data.get('id'),
                    'transaction_id': transaction_id,
                    'amount': amount,
                    'currency': currency,
                    'status': 'pending',
                    'phone': phone_number,
                    'reference': reference,
                    'description': description,
                    'payment_link_url': data.get('wave_launch_url') or data.get('checkout_url'),
                    'wave_response': json.dumps(data),
                    'account_move_id': account_move.id,
                    'partner_id': partner.id,
                    'checkout_status': data.get('checkout_status'),
                    'payment_status': data.get('payment_status'),
                })

                return {
                    'success': True,
                    'transaction_id': wave_transaction.transaction_id,
                    'wave_id': data.get('id'),
                    'session_id': data.get('id'),
                    'payment_url': data.get('wave_launch_url') or data.get('checkout_url'),
                    'status': 'pending',
                    'account_move_id': wave_transaction.account_move_id.id,
                    'partner_id': wave_transaction.partner_id.id,
                    'reference': reference,
                    'checkout_status': data.get('checkout_status'),
                    'payment_status': data.get('payment_status'),
                }
            else:
                _logger.error(f"Wave API Error: {response.status_code} - {response.text}")
                return {'error': response.text, 'success': False}

        except Exception as e:
            _logger.error(f"Error initiating Wave payment: {str(e)}")
            return {'error': str(e), 'success': False}
