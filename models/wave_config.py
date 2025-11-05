
from odoo import models, fields, api
from odoo.exceptions import ValidationError

class WaveConfig(models.Model):
    _name = 'wave.config'
    _description = 'Configuration Wave Money'
    _rec_name = 'name'

    name = fields.Char(
        string='Nom de la configuration', 
        required=True,
        help="Nom descriptif pour cette configuration Wave"
    )
    
    api_key = fields.Char(
        string='Clé API Wave', 
        required=True,
        help="Clé API fournie par Wave pour l'authentification"
    )
    
    webhook_secret = fields.Char(
        string='Secret Webhook', 
        required=True,
        help="Secret utilisé pour vérifier l'authenticité des webhooks Wave"
    )
    webhook_claire = fields.Char(
        string='Secret Webhook Claire', 
        required=True,
        help="Secret utilisé pour vérifier l'authenticité des webhooks Wave"
    )
    

    callback_url = fields.Char(
        string='URL de Callback', 
        required=True,
        default='https://orbitcity.sn/wave/payment/callback',
        help="URL vers laquelle l'utilisateur sera redirigé après le paiement"
    )
    
    webhook_url = fields.Char(
        string='URL de Webhook', 
        required=True,
        default='https://orbitcity.sn/wave/webhook',
        help="URL que Wave utilisera pour envoyer les notifications de statut"
    )
    
    is_active = fields.Boolean(
        string='Configuration Active', 
        default=True,
        help="Seule une configuration peut être active à la fois"
    )
    
    environment = fields.Selection([
        ('sandbox', 'Sandbox (Test)'),
        ('production', 'Production')
    ], string='Environnement', default='sandbox', required=True)
    
    default_currency = fields.Selection([
        ('XOF', 'Franc CFA (XOF)'),
        ('USD', 'Dollar US (USD)'),
        ('EUR', 'Euro (EUR)')
    ], string='Devise par défaut', default='XOF', required=True)
    
    # Champs de suivi
    created_at = fields.Datetime(
        string='Date de création', 
        default=fields.Datetime.now,
        readonly=True
    )
    
    updated_at = fields.Datetime(
        string='Dernière modification', 
        default=fields.Datetime.now,
        readonly=True
    )
    
    # Statistiques
    total_transactions = fields.Integer(
        string='Total des transactions',
        compute='_compute_transaction_stats',
        store=False
    )
    
    successful_transactions = fields.Integer(
        string='Transactions réussies',
        compute='_compute_transaction_stats',
        store=False
    )
    
    failed_transactions = fields.Integer(
        string='Transactions échouées',
        compute='_compute_transaction_stats',
        store=False
    )

    @api.depends('is_active')
    def _compute_transaction_stats(self):
        """Calculer les statistiques des transactions"""
        for record in self:
            transactions = self.env['wave.transaction'].search([])
            record.total_transactions = len(transactions)
            record.successful_transactions = len(transactions.filtered(lambda t: t.status == 'completed'))
            record.failed_transactions = len(transactions.filtered(lambda t: t.status == 'failed'))

    @api.constrains('is_active')
    def _check_single_active_config(self):
        """S'assurer qu'une seule configuration est active"""
        if self.is_active:
            other_active = self.search([('is_active', '=', True), ('id', '!=', self.id)])
            if other_active:
                raise ValidationError("Une seule configuration Wave peut être active à la fois.")

    def write(self, vals):
        """Mettre à jour la date de modification"""
        vals['updated_at'] = fields.Datetime.now()
        return super().write(vals)

    def action_view_transactions(self):
        """Action pour voir toutes les transactions"""
        return {
            'type': 'ir.actions.act_window',
            'name': 'Transactions Wave',
            'res_model': 'wave.transaction',
            'view_mode': 'tree,form',
            'domain': [],
            'context': {'create': False},
            'target': 'current',
        }

    def action_view_successful_transactions(self):
        """Action pour voir les transactions réussies"""
        return {
            'type': 'ir.actions.act_window',
            'name': 'Transactions Wave Réussies',
            'res_model': 'wave.transaction',
            'view_mode': 'tree,form',
            'domain': [('status', '=', 'completed')],
            'context': {'create': False},
            'target': 'current',
        }

    def action_view_failed_transactions(self):
        """Action pour voir les transactions échouées"""
        return {
            'type': 'ir.actions.act_window',
            'name': 'Transactions Wave Échouées',
            'res_model': 'wave.transaction',
            'view_mode': 'tree,form',
            'domain': [('status', '=', 'failed')],
            'context': {'create': False},
            'target': 'current',
        }

    def action_test_webhook(self):
        """Tester l'URL du webhook"""
        try:
            if not self.webhook_url:
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': 'Webhook non configuré',
                        'message': 'Veuillez configurer l\'URL du webhook avant de tester.',
                        'type': 'warning',
                    }
                }
            
            import requests
            
            # Test simple de ping vers l'URL webhook
            response = requests.get(self.webhook_url, timeout=10)
            
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Test webhook',
                    'message': f'Webhook accessible. Status: {response.status_code}',
                    'type': 'success' if response.status_code < 400 else 'warning',
                }
            }
            
        except Exception as e:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Erreur webhook',
                    'message': f'Erreur lors du test webhook: {str(e)}',
                    'type': 'danger',
                }
            }
        
    def test_connection(self):
        """Tester la connexion à l'API Wave"""
        try:
            import requests

            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            }

            # Test avec un endpoint compatible avec checkout_api
            # Créer un paiement de test minimal pour vérifier la connexion
            test_payload = {
                "amount": 100,  # Montant minimal pour test
                "currency": self.default_currency,
                "success_url": "https://orbitcity.sn/api/wave/webhook",  # Utiliser callback_url comme success_url
                "error_url": "https://orbitcity.sn/api/wave/webhook",  # Utiliser callback_url comme error_url
            }

            # Utiliser l'endpoint de création de checkout sessions qui fonctionne avec checkout_api
            response = requests.post(
                "https://api.wave.com/v1/checkout/sessions",
                json=test_payload,
                headers=headers,
                timeout=10
            )

            if response.status_code == 201 :
                # Succès - supprimer le paiement de test si possible
                data = response.json()
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': 'Connexion réussie',
                        'message': f'La connexion à l\'API Wave a été établie avec succès. Détails de la réponse : {data}',
                        'type': 'success',
                    }
                }
            elif response.status_code == 200:
                # Succès avec code 200
                data = response.json()
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': 'Test réussi',
                        'message': f'La connexion à l\'API Wave a été établie avec succès. Détails de la réponse : {data}',
                        'type': 'success',
                    }
                }
            elif response.status_code == 403:
                error_data = response.json() if response.content else {}
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': 'Erreur de permissions',
                        'message': f'Votre clé API n\'a pas les bonnes permissions. Détails: {error_data.get("message", response.text)}',
                        'type': 'warning',
                    }
                }
            elif response.status_code == 403:
                error_data = response.json() if response.content else {}
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': 'Erreur de permissions',
                        'message': f'Votre clé API n\'a pas les bonnes permissions. Détails: {error_data.get("message", response.text)}',
                        'type': 'warning',
                    }
                }
            else:
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': 'Erreur de connexion',
                        'message': f'Erreur {response.status_code}: {response.text}',
                        'type': 'danger',
                    }
                }

        except Exception as e:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Erreur de connexion',
                    'message': f'Erreur: {str(e)}',
                    'type': 'danger',
                }
            }
        
    def get_session_by_id(self, session_id):
        """Récupérer une session de paiement par son ID"""
        try:
            import requests

            headers = {
                "Authorization": f"Bearer {self.api_key}",  
                "Content-Type": "application/json",
            }
            response = requests.get(
                f"https://api.wave.com/v1/checkout/sessions/{session_id}",
                headers=headers,
                timeout=10
            )

            if response.status_code == 200:
                return response.json()
            else:
                return None

        except Exception as e:
            return None
    def get_seesion_by_id_transaction(self, transaction_id):
        """Récupérer une session de paiement par son ID de transaction"""
        try:
            import requests

            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            }
            response = requests.get(
                f"https://api.wave.com/v1/checkout/sessions?transaction_id={transaction_id}",
                headers=headers,
                timeout=10
            )

            if response.status_code == 200:
                return response.json()
            else:
                return None

        except Exception as e:
            return None
        
    def refund_transaction(self, session_id):
        """Rembourser une transaction Wave"""
        try:
            import requests

            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            }
        
            response = requests.post(
                f"https://api.wave.com/v1/checkout/sessions/{session_id}/refund",
                headers=headers,
                timeout=10
            )
            if response.status_code == 200:
                return response.json()
            else:
                return None

        except Exception as e:
            return None

