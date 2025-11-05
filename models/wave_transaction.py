
from odoo import models, fields, api
import json
from odoo.exceptions import ValidationError
import logging
import base64
import io
from datetime import datetime

_logger = logging.getLogger(__name__)

class WaveTransaction(models.Model):
    _name = 'wave.transaction'
    _description = 'Transaction Wave Money'
    _order = 'created_at desc'
    _rec_name = 'reference'

    # Identifiants
    wave_id = fields.Char(
        string="ID Wave",
        required=True,
        index=True,
        help="Identifiant unique de la transaction chez Wave"
    )
    transaction_id = fields.Char(
        string="ID de transaction",
        required=True,
        index=True,
        help="Identifiant unique de la transaction dans Odoo"
    )

    reference = fields.Char(
        string="Référence",
        required=True,
        index=True,
        help="Référence unique de la transaction côté client"
    )
    # Informations de paiement
    amount = fields.Float(
        string="Montant",
        required=True,
        digits=(16, 2),
        help="Montant de la transaction"
    )

    currency = fields.Selection([
        ('XOF', 'Franc CFA (XOF)'),
        ('USD', 'Dollar US (USD)'),
        ('EUR', 'Euro (EUR)')
    ], string='Devise', default='XOF', required=True)

    phone = fields.Char(
        string="Numéro de téléphone",
        help="Numéro de téléphone du payeur"
    )

    description = fields.Text(
        string="Description",
        help="Description de la transaction"
    )
    # Statut et suivi
    status = fields.Selection([
        ('pending', 'En attente'),
        ('completed', 'Complété'),
        ('failed', 'Échoué'),
        ('cancelled', 'Annulé'),
        ('expired', 'Expiré'),
        ('refunded', 'Remboursé')
    ], string='Statut', default='pending', required=True, index=True)

    checkout_status = fields.Selection([
        ('open', 'Ouvert'),
        ('complete', 'Complété'),
        ('expired', 'Expiré')
    ], string='Statut de checkout', help="Statut de checkout de Wave")
    
    payment_status = fields.Selection([
        ('processing', 'En cours'),
        ('cancelled', 'Annulé'),
        ('succeeded', 'Réussi')
    ], string='Statut de paiement', help="Statut de paiement de Wave")
    # URLs et données
    payment_link_url = fields.Char(
        string="URL de paiement",
        help="URL générée par Wave pour effectuer le paiement"
    )

    # NOUVEAU CHAMP POUR LA FACTURE
    url_facture = fields.Char(
        string="URL de la facture",
        help="URL vers le fichier PDF de la facture générée"
    )

    facture_pdf = fields.Binary(
        string="Facture PDF",
        help="Fichier PDF de la facture"
    )

    facture_filename = fields.Char(
        string="Nom du fichier facture",
        help="Nom du fichier PDF de la facture"
    )

    facture_generated_at = fields.Datetime(
        string="Date de génération de la facture",
        help="Date à laquelle la facture a été générée"
    )

    facture_size = fields.Integer(
        string="Taille de la facture",
        help="Taille du fichier PDF de la facture en octets"
    )

    wave_response = fields.Text(
        string="Réponse Wave",
        help="Réponse complète de l'API Wave lors de la création"
    )

    webhook_data = fields.Text(
        string="Données Webhook",
        help="Dernières données reçues via webhook"
    )
    # Relations
    account_move_id = fields.Many2one(
        'account.move',
        string="Facture liée",
        help="Facture de vente associée à cette transaction"
    )

    partner_id = fields.Many2one(
        'res.partner',
        string="Client",
        help="Client associé à cette transaction"
    )
    # Dates
    created_at = fields.Datetime(
        string="Date de création",
        default=fields.Datetime.now,
        required=True,
        readonly=True
    )

    updated_at = fields.Datetime(
        string="Dernière mise à jour",
        default=fields.Datetime.now,
        readonly=True
    )

    completed_at = fields.Datetime(
        string="Date de completion",
        readonly=True,
        help="Date à laquelle la transaction a été complétée"
    )
    # Champs calculés
    status_color = fields.Integer(
        string="Couleur du statut",
        compute='_compute_status_color',
        store=False
    )

    formatted_amount = fields.Char(
        string="Montant formaté",
        compute='_compute_formatted_amount',
        store=False
    )
    auto_saved = fields.Boolean(
        string="Enregistré automatiquement",
        default=True,
        help="Indique si les informations de la facture ont été enregistrées automatiquement"
    )


    @api.depends('status')
    def _compute_status_color(self):
        """Calculer la couleur selon le statut"""
        color_map = {
            'pending': 4,     # Bleu
            'completed': 10, # Vert
            'failed': 1,      # Rouge
            'cancelled': 3,  # Jaune
            'expired': 8,     # Gris
            'refunded': 9     # Violet
        }
        for record in self:
            record.status_color = color_map.get(record.status, 0)

    @api.depends('amount', 'currency')
    def _compute_formatted_amount(self):
        """Formater le montant avec la devise"""
        for record in self:
            if record.currency == 'XOF':
                record.formatted_amount = f"{record.amount:,.0f} FCFA"
            else:
                record.formatted_amount = f"{record.amount:,.2f} {record.currency}"


    def _generate_invoice_pdf(self):
        """Générer la facture PDF pour la transaction"""
        try:
            _logger.info(f"Génération de la facture PDF pour la transaction {self.transaction_id}")

            # Créer le contenu HTML de la facture
            html_content = self._get_invoice_html_content()

            # Générer le PDF à partir du HTML
            pdf_content = self._html_to_pdf(html_content)

            if pdf_content:
                # Générer le nom du fichier
                filename = f"facture_wave_{self.transaction_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"

                # Encoder le PDF en base64
                pdf_base64 = base64.b64encode(pdf_content).decode('utf-8')

                # Créer un attachement
                attachment = self.env['ir.attachment'].create({
                    'name': filename,
                    'type': 'binary',
                    'datas': pdf_base64,
                    'res_model': self._name,
                    'res_id': self.id,
                    'mimetype': 'application/pdf',
                    'public': True,  # Rendre accessible publiquement
                })

                # Construire l'URL d'accès
                base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
                url_facture = f"{base_url}/web/content/{attachment.id}/{filename}"

                # Mettre à jour les champs de la transaction
                self.write({
                    'facture_pdf': pdf_base64,
                    'facture_filename': filename,
                    'url_facture': url_facture,
                    'facture_generated_at': fields.Datetime.now(),
                    'facture_size': len(pdf_content)
                })

                # Enregistrer automatiquement les informations
                self._auto_save_invoice_info()

                _logger.info(f"Facture PDF générée avec succès: {url_facture}")
                return url_facture
            else:
                _logger.error("Erreur lors de la génération du PDF")
                return False

        except Exception as e:
            _logger.error(f"Erreur lors de la génération de la facture PDF: {str(e)}")
            return False

    def _get_invoice_html_content(self):
        """Générer le contenu HTML de la facture avec le logo CCBM"""
        # Récupérer les informations de l'entreprise
        company = self.env.company

        # Formatage de la date
        date_facture = self.completed_at.strftime('%d/%m/%Y %H:%M:%S') if self.completed_at else datetime.now().strftime('%d/%m/%Y %H:%M:%S')

        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8"/>
            <title>Facture Wave - {self.reference}</title>
            <style>
                body {{ font-family: Arial, sans-serif; margin: 0; padding: 20px; }}
                .header {{ text-align: center; margin-bottom: 30px; border-bottom: 2px solid #2879b9; padding-bottom: 20px; }}
                .company-section {{ display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 30px; }}
                .company-info {{ flex: 1; text-align: left; }}
                .company-logo {{ flex: 0 0 200px; text-align: right; }}
                .company-logo img {{ max-width: 180px; max-height: 120px; object-fit: contain; }}
                .invoice-info {{ background-color: #f8f9fa; padding: 15px; border-radius: 5px; margin-bottom: 20px; }}
                .transaction-details {{ margin-bottom: 20px; }}
                .table {{ width: 100%; border-collapse: collapse; margin-bottom: 20px; }}
                .table th, .table td {{ border: 1px solid #dee2e6; padding: 8px; text-align: left; }}
                .table th {{ background-color: #2879b9; color: white; }}
                .total {{ font-size: 18px; font-weight: bold; text-align: right; margin-top: 20px; }}
                .footer {{ margin-top: 40px; text-align: center; font-size: 12px; color: #6c757d; }}
                .status-success {{ color: #28a745; font-weight: bold; }}
                .ccbm-branding {{ color: #2879b9; font-weight: bold; }}
            </style>
        </head>
        <body>

            <div class="header"
                style="display: flex; justify-content: space-between; align-items: center; border-bottom: 2px solid #2879b9; padding-bottom: 20px; margin-bottom: 30px;">
                <div style="text-align: left;">
                    <h2 class="ccbm-branding" style="margin: 0;">FACTURE DE PAIEMENT</h2>
                    <h3 style="margin: 5px 0 0;">Référence: {self.reference}</h3>
                </div>
                <div class="company-logo" style="text-align: right;">
                    <img src="https://ccbmshop.sn/logo.png" alt="CCTS Logo"
                        style="max-width: 180px; max-height: 120px; object-fit: contain;" onerror="this.style.display='none'" />
                </div>
            </div>

            
            <div class="company-section" style="display: flex; justify-content: space-between; align-items: center; gap: 20px;">
                <div class="company-info">
                    <h3 class="ccbm-branding">CCTS</h3>
                    <p><strong>Adresse:</strong> {company.street or 'Dakar, Sénégal'}</p>
                    <p><strong>Ville:</strong> {company.city or 'Dakar'}, {company.country_id.name or 'Sénégal'}</p>
                    <p><strong>Téléphone:</strong> {company.phone or '70 922 17 75 | 70 843 04 36'}</p>
                    <p><strong>Email:</strong> {company.email or 'shop@ccbm.sn'}</p>
                    <p><strong>Site Web:</strong> www.ccts.sn</p>
                </div>
            </div>

            <div class="invoice-info">
                <h3>Informations de la facture</h3>
                <p><strong>Numéro de facture:</strong> WAVE-{self.id:06d}</p>
                <p><strong>Date de paiement:</strong> {date_facture}</p>
                <p><strong>Statut:</strong> <span class="status-success">PAYÉ</span></p>
                <p><strong>Mode de paiement:</strong> Wave Money</p>
            </div>

            <div class="transaction-details">
                <h3>Détails de la transaction</h3>
                <table class="table">
                    <tr>
                        <th>Transaction ID</th>
                        <td>{self.transaction_id}</td>
                    </tr>
                    <tr>
                        <th>Wave ID</th>
                        <td>{self.wave_id}</td>
                    </tr>
                    <tr>
                        <th>Téléphone</th>
                        <td>{self.phone or 'N/A'}</td>
                    </tr>
                    <tr>
                        <th>Description</th>
                        <td>{self.description or 'Paiement via Wave Money'}</td>
                    </tr>
        """

        # Ajouter les détails de la commande si elle existe
        if self.account_move_id:
            html_content += f"""
                    <tr>
                        <th>Commande liée</th>
                        <td>{self.account_move_id.name}</td>
                    </tr>
            """

        # Ajouter les détails du client si il existe
        if self.partner_id:
            html_content += f"""
                    <tr>
                        <th>Client</th>
                        <td>{self.partner_id.name}</td>
                    </tr>
                    <tr>
                        <th>Email Client</th>
                        <td>{self.partner_id.email or 'N/A'}</td>
                    </tr>
            """

        html_content += f"""
                </table>
            </div>

            <div class="total">
                <p>MONTANT TOTAL PAYÉ: <span class="ccbm-branding">{self.formatted_amount}</span></p>
            </div>

            <div class="footer">
                <p><strong class="ccbm-branding">CCTS</strong> </p>
                <p style="margin-top: 15px;">
                    <strong>Contacts:</strong> 70 922 17 75 | 70 843 04 36<br>
                    <strong>Email:</strong> contact@ccts.sn | <strong>Web:</strong> www.ccts.sn
                </p>
            </div>
        </body>
        </html>
        """

        return html_content
    

    def _html_to_pdf(self, html_content):
        """Convertir le HTML en PDF"""
        try:
            # Utiliser wkhtmltopdf via Odoo
            return self.env['ir.actions.report']._run_wkhtmltopdf(
                [html_content],
                landscape=False,
                specific_paperformat_args={
                    'data-report-margin-top': 10,
                    'data-report-margin-bottom': 10,
                    'data-report-margin-left': 10,
                    'data-report-margin-right': 10,
                    'data-report-page-size': 'A4',
                }
            )
        except Exception as e:
            _logger.error(f"Erreur lors de la conversion HTML vers PDF: {str(e)}")
            return False

    def _auto_save_invoice_info(self):
        """Enregistrer automatiquement les informations après génération de la facture"""
        try:
            _logger.info(f"Enregistrement automatique des informations pour la transaction {self.transaction_id}")

            # Créer un enregistrement dans un modèle de log ou historique
            invoice_log_data = {
                'transaction_id': self.transaction_id,
                'wave_id': self.wave_id,
                'reference': self.reference,
                'amount': self.amount,
                'currency': self.currency,
                'phone': self.phone,
                'partner_name': self.partner_id.name if self.partner_id else 'N/A',
                'account_move_name': self.account_move_id.name if self.account_move_id else 'N/A',
                'facture_url': self.url_facture,
                'facture_filename': self.facture_filename,
                'facture_size': self.facture_size,
                'generated_at': self.facture_generated_at,
                'status': 'completed'
            }

            # Enregistrer dans les logs système
            _logger.info(f"Facture générée et enregistrée: {json.dumps(invoice_log_data, default=str)}")

            # Marquer comme enregistré automatiquement
            self.write({'auto_saved': True})

            # Optionnel: Envoyer une notification ou email
            self._send_invoice_notification()

            return True

        except Exception as e:
            _logger.error(f"Erreur lors de l'enregistrement automatique: {str(e)}")
            return False

    def _send_invoice_notification(self):
        """Envoyer une notification après génération de la facture"""
        try:
            if self.partner_id and self.partner_id.email:
                # Créer le message email
                body_html = f"""
                    <p>Bonjour {self.partner_id.name},</p>
                    <p>Votre paiement Wave a été traité avec succès.</p>
                    <p><strong>Détails:</strong></p>
                    <ul>
                        <li>Transaction ID: {self.transaction_id}</li>
                        <li>Montant: {self.formatted_amount}</li>
                        <li>Date: {self.completed_at.strftime('%d/%m/%Y %H:%M:%S') if self.completed_at else 'N/A'}</li>
                    </ul>
                    <p>Vous pouvez télécharger votre facture <a href="{self.url_facture}">ici</a>.</p>
                    <p>Merci pour votre confiance,<br>L'équipe CCTS</p>
                """

                sujet = f'Facture Wave - {self.reference}'
                # Envoyer l'email
                mail_server = self.env['ir.mail_server'].sudo().search([], limit=1)        
                email_from = mail_server.smtp_user
                if not email_from:
                    email_from = 'ccbmtech@ccbm.sn'
 
                additional_email = 'alhussein.khouma@ccbm.sn'
                email_to = f'{self.partner_id.email}, {additional_email}'

                email_values = {
                    'email_from': email_from,
                    'email_to': email_to,
                    'subject': sujet,
                    'body_html': body_html,
                    'state': 'outgoing',
                }
                mail_mail = self.env['mail.mail'].sudo().create(email_values)
                try:
                    mail_mail.send()
                    return True
                except Exception as e:
                    return False
            return False 

        except Exception as e:
            _logger.error(f"Erreur lors de l'envoi de la notification: {str(e)}")

    def write(self, vals):
        """Surcharger write pour mettre à jour la date de modification et générer la facture"""
        if 'status' in vals:
            _logger.info(f"Changing status of transaction {self.id} from {self.status} to {vals['status']}")

        vals['updated_at'] = fields.Datetime.now()

        # Si le statut passe à 'completed', enregistrer la date et générer la facture
        if vals.get('status') == 'completed' and self.status != 'completed':
            vals['completed_at'] = fields.Datetime.now()

            # Appeler la méthode de génération de facture après la mise à jour
            result = super().write(vals)

            # Générer la facture PDF de manière asynchrone pour éviter les blocages
            try:
                self._generate_invoice_pdf()
                _logger.info(f"Facture générée avec succès pour la transaction {self.transaction_id}")
            except Exception as e:
                _logger.error(f"Erreur lors de la génération de la facture pour la transaction {self.transaction_id}: {str(e)}")

                # Créer le paiement et relier la facture
            try:
                self._create_payment_and_link_invoice()
                _logger.info(f"Paiement et facture créés avec succès pour la transaction {self.transaction_id}")
            except Exception as e:
                _logger.error(f"Erreur lors de la création du paiement et de la facture pour la transaction {self.transaction_id}: {str(e)}")


            return result

        return super().write(vals)

    @api.model
    def create(self, vals):
        """Surcharger create pour ajouter des validations"""
        # Vérifier l'unicité du transaction_id
        if vals.get('transaction_id'):
            existing = self.search([('transaction_id', '=', vals['transaction_id'])])
            if existing:
                raise ValidationError(f"Une transaction avec l'ID '{vals['transaction_id']}' existe déjà.")

        # Vérifier l'unicité de la référence
        if vals.get('reference'):
            existing = self.search([('reference', '=', vals['reference'])])
            if existing:
                raise ValidationError(f"Une transaction avec la référence '{vals['reference']}' existe déjà.")

        return super().create(vals)


    def action_refresh_status(self):
        """Action pour rafraîchir le statut depuis Wave"""
        try:
            config = self.env['wave.config'].search([('is_active', '=', True)], limit=1)
            if not config:
                raise ValidationError("Aucune configuration Wave active trouvée.")
            # Utiliser la méthode du modèle pour récupérer la session
            session_data = config.get_session_by_id(self.wave_id)
            _logger.info(f"Wave session data: {session_data}")
            if session_data:
                # Vérifier checkout_status et payment_status
                checkout_status = session_data.get('checkout_status', '').lower()
                payment_status = session_data.get('payment_status', '').lower()
                self.write({
                    'checkout_status': checkout_status,
                    'payment_status': payment_status
                })
                # Déterminer le statut en fonction des deux champs
                if checkout_status == 'complete' and payment_status == 'succeeded':
                    wave_status = 'completed'
                elif checkout_status == 'failed' or payment_status == 'failed':
                    wave_status = 'failed'
                elif checkout_status == 'cancelled' or payment_status == 'cancelled':
                    wave_status = 'cancelled'
                elif checkout_status == 'expired':
                    wave_status = 'expired'
                else:
                    wave_status = 'pending'
                # Mapper le statut Wave vers Odoo
                status_mapping = {
                    'completed': 'completed',
                    'succeeded': 'completed',
                    'failed': 'failed',
                    'cancelled': 'cancelled',
                    'canceled': 'cancelled',
                    'pending': 'pending',
                    'processing': 'pending',
                    'expired': 'expired'
                }
                new_status = status_mapping.get(wave_status, 'pending')
                if new_status != self.status:
                    _logger.info(f"Updating status from manual refresh for transaction {self.id} to {new_status}")
                    self.write({
                        'status': new_status,
                        'wave_response': json.dumps(session_data),
                    })
                    return {
                        'type': 'ir.actions.client',
                        'tag': 'display_notification',
                        'params': {
                            'title': 'Statut mis à jour',
                            'message': f'Le statut a été mis à jour: {new_status}',
                            'type': 'success',
                        }
                    }
            else:
                raise ValidationError("Impossible de récupérer les données de la session Wave")
        except Exception as e:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Erreur',
                    'message': f'Erreur lors de la mise à jour: {str(e)}',
                    'type': 'danger',
                }
            }

    def action_download_invoice(self):
        """Action pour télécharger la facture PDF"""
        if self.facture_pdf:
            return {
                'type': 'ir.actions.act_url',
                'url': f'/web/content?model={self._name}&id={self.id}&field=facture_pdf&filename_field=facture_filename&download=true',
                'target': 'self',
            }
        else:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Aucune facture',
                    'message': 'Aucune facture disponible pour cette transaction.',
                    'type': 'warning',
                }
            }

    def action_view_invoice_url(self):
        """Action pour ouvrir l'URL de la facture"""
        if self.url_facture:
            return {
                'type': 'ir.actions.act_url',
                'url': self.url_facture,
                'target': 'new',
            }
        else:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Aucune facture',
                    'message': 'Aucune URL de facture disponible pour cette transaction.',
                    'type': 'warning',
                }
            }

    def action_regenerate_invoice(self):
        """Action pour régénérer la facture manuellement"""
        if self.status == 'completed':
            try:
                url_facture = self._generate_invoice_pdf()
                if url_facture:
                    return {
                        'type': 'ir.actions.client',
                        'tag': 'display_notification',
                        'params': {
                            'title': 'Facture régénérée',
                            'message': f'La facture a été régénérée avec succès: {url_facture}',
                            'type': 'success',
                        }
                    }
                else:
                    return {
                        'type': 'ir.actions.client',
                        'tag': 'display_notification',
                        'params': {
                            'title': 'Erreur',
                            'message': 'Erreur lors de la régénération de la facture.',
                            'type': 'danger',
                        }
                    }
            except Exception as e:
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': 'Erreur',
                        'message': f'Erreur lors de la régénération: {str(e)}',
                        'type': 'danger',
                    }
                }
        else:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Transaction non complétée',
                    'message': 'La facture ne peut être générée que pour les transactions complétées.',
                    'type': 'warning',
                }
            }

    # Autres méthodes existantes...
    def action_view_payment_link(self):
        """Action pour ouvrir le lien de paiement"""
        if self.payment_link_url:
            return {
                'type': 'ir.actions.act_url',
                'url': self.payment_link_url,
                'target': 'new',
            }
        else:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Aucun lien',
                    'message': 'Aucun lien de paiement disponible pour cette transaction.',
                    'type': 'warning',
                }
            }

    def action_view_invoice(self):
        """Action pour ouvrir la facture associée associée"""
        if self.account_move_id:
            return {
                'type': 'ir.actions.act_window',
                'name': 'Facture',
                'res_model': 'account.move',
                'res_id': self.account_move_id.id,
                'view_mode': 'form',
                'target': 'current',
            }
        else:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Aucune facture',
                    'message': 'Aucune facture associée à cette transaction.',
                    'type': 'warning',
                }
            }


    def _create_payment_and_link_invoice(self):
        """Créer un paiement et relier à une facture pour une transaction réussie"""
        try:
            _logger.info(f"Création du paiement et liaison de la facture pour la transaction {self.transaction_id}")

            # Vérifier que la transaction est bien complétée
            if self.status != 'completed':
                _logger.warning(f"La transaction {self.transaction_id} n'est pas complétée. Aucun paiement créé.")
                return False

            # Récupérer les informations nécessaires
            account_move = self.account_move_id
            partner = self.partner_id
            company = self.env.company

            # Rechercher un journal de vente
            journal = self.env['account.journal'].search([
                ('type', '=', 'sale'),
                ('company_id', '=', company.id)
            ], limit=1)

            if not journal:
                _logger.error("Aucun journal de vente trouvé pour la compagnie.")
                return False

            # Rechercher une méthode de paiement
            payment_method = self.env['account.payment.method'].search([('payment_type', '=', 'inbound')], limit=1)
            payment_method_line = self.env['account.payment.method.line'].search([
                ('payment_method_id', '=', payment_method.id),
                ('journal_id', '=', journal.id)
            ], limit=1)

            if not payment_method_line:
                _logger.error("Aucune méthode de paiement trouvée.")
                return False

            

            # Créer le paiement
            payment = self.env['account.payment'].create({
                'payment_type': 'inbound',
                'partner_type': 'customer',
                'partner_id': partner.id,
                'amount': self.amount,
                'journal_id': journal.id,
                'currency_id': partner.currency_id.id or account_move.currency_id.id or journal.currency_id.id,
                'payment_method_line_id': payment_method_line.id,
                'payment_method_id': payment_method.id,
                # 'ref': order.name,
                'destination_account_id': partner.property_account_receivable_id.id,
                'move_id': account_move.id
            })

            # Valider le paiement
            payment.action_post()

            # Relier le paiement à la facture
            account_move.js_assign_outstanding_line(payment.line_ids[0].id)

            _logger.info(f"Paiement et facture créés avec succès pour la transaction {self.transaction_id}")
            return True

        except Exception as e:
            _logger.error(f"Erreur lors de la création du paiement et de la facture: {str(e)}")
            return False




    _sql_constraints = [
        ('transaction_id_unique', 'UNIQUE(transaction_id)', 'L\'ID de transaction doit être unique.'),
        ('reference_unique', 'UNIQUE(reference)', 'La référence doit être unique.'),
    ]


    

class SaleOrder(models.Model):
    _inherit = 'account.move'

    wave_transaction_ids = fields.One2many('wave.transaction', 'account_move_id', string='Transactions Wave')

    def action_view_wave_transactions(self):
        return {
            'name': 'Transactions Wave',
            'type': 'ir.actions.act_window',
            'view_mode': 'tree',
            'res_model': 'wave.transaction',
            'domain': [('account_move_id', '=', self.id)],
        }
