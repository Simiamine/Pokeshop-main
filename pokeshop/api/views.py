import stripe
from django.contrib.auth import authenticate
from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings
from rest_framework import viewsets, status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.decorators import action
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework_simplejwt.authentication import JWTAuthentication
from .models import Utilisateur, Commande, Pokedex, Paiement, Avis
from .serializers import UtilisateurSerializer, CommandeSerializer, PokedexSerializer, PaiementSerializer, AvisSerializer

# Suivi et mise à jour du statut via Webhooks

@csrf_exempt
def stripe_webhook(request):
    print("Webhook reçu") 
    payload = request.body
    sig_header = request.META['HTTP_STRIPE_SIGNATURE']
    event = None

    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, settings.STRIPE_WEBHOOK_SECRET
        )
        print(f"Événement Stripe reçu : {event['type']}")
    except ValueError as e:
        # Invalid payload
        return JsonResponse({'error': 'Invalid payload'}, status=400)
    except stripe.error.SignatureVerificationError as e:
        # Invalid signature
        return JsonResponse({'error': 'Invalid signature'}, status=400)

    # Gérer l'événement selon son type
    if event['type'] == 'checkout.session.completed':
        session = event['data']['object']
        
        # Log du Transaction ID reçu
        print(f"Transaction ID reçu du webhook : {session.id}")
        
        try:
            paiement = Paiement.objects.get(transaction_id=session.id)
            paiement.statut = 'valide'
            paiement.save()
        except Paiement.DoesNotExist:
            print(f"Aucun paiement trouvé avec transaction_id : {session.id}")
            return JsonResponse({'error': f"Aucun paiement trouvé avec transaction_id : {session.id}"}, status=404)

    return JsonResponse({'status': 'success'}, status=200)

# Vue pour gérer les utilisateurs
class UtilisateurViewSet(viewsets.ModelViewSet):
    queryset = Utilisateur.objects.all()
    serializer_class = UtilisateurSerializer

    # Accéder au profil d'un utilisateur spécifique
    @action(detail=True, methods=['get'])
    def profil(self, request, pk=None):
        utilisateur = get_object_or_404(Utilisateur, pk=pk)
        serializer = self.get_serializer(utilisateur)
        return Response(serializer.data)

    # Accéder à l'historique des commandes d'un utilisateur
    @action(detail=True, methods=['get'])
    def commandes(self, request, pk=None):
        utilisateur = get_object_or_404(Utilisateur, pk=pk)
        commandes = Commande.objects.filter(utilisateur_id=utilisateur.id)
        serializer = CommandeSerializer(commandes, many=True)
        return Response(serializer.data)
    
    #Pareil mais si authentifie accede directement aux commandes
    @action(detail=False, methods=['get'], url_path='mes-commandes', permission_classes=[AllowAny])
    def mes_commandes(self, request):
        jwt_authenticator = JWTAuthentication()
        response = jwt_authenticator.authenticate(request)
        if response is None:
            return Response({"detail": "Token invalid or missing"}, status=401)
        utilisateur, token = response
        commandes = Commande.objects.filter(utilisateur=utilisateur)
        serializer = CommandeSerializer(commandes, many=True)
        return Response(serializer.data)

# Vue pour enregistrer un utilisateur
class UserRegisterView(APIView):
    def post(self, request):
        serializer = UtilisateurSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    
# Vue pour modifier ou supprimer un utilisateur
class UserUpdateDeleteView(APIView):
    def put(self, request, pk):
        utilisateur = get_object_or_404(Utilisateur, pk=pk)
        serializer = UtilisateurSerializer(utilisateur, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    
    def delete(self, request, pk):
        utilisateur = get_object_or_404(Utilisateur, pk=pk)
        utilisateur.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

# Vue pour gérer les commandes
class CommandeViewSet(viewsets.ModelViewSet):
    queryset = Commande.objects.all()
    serializer_class = CommandeSerializer

    def perform_create(self, serializer):
        # Permet de récupérer l'ID de l'utilisateur depuis le body de la requête
        utilisateur = self.request.user
        serializer.save()

    # Suivre l'état de la livraison
    @action(detail=True, methods=['get'], url_path='suivi-livraison')
    def suivi_livraison(self, request, pk=None):
        commande = self.get_object()
        return Response({
            "numero_commande": commande.numero_commande,
            "statut": commande.statut,
            "adresse_livraison": commande.adresse_livraison,
            "ville": commande.ville,
            "code_postal": commande.code_postal
        })

    # Mettre à jour les informations de livraison
    @action(detail=True, methods=['patch'], url_path='update-livraison')  # Autoriser PATCH ici
    def update_livraison(self, request, pk=None):
        commande = self.get_object()
        # Mettre à jour uniquement les champs fournis dans la requête PATCH
        commande.adresse_livraison = request.data.get('adresse_livraison', commande.adresse_livraison)
        commande.ville = request.data.get('ville', commande.ville)
        commande.code_postal = request.data.get('code_postal', commande.code_postal)
        commande.statut = request.data.get('statut', commande.statut)
        commande.save()
        
        return Response({
            "message": "Les informations de livraison ont été mises à jour.",
            "adresse_livraison": commande.adresse_livraison,
            "ville": commande.ville,
            "code_postal": commande.code_postal,
            "statut": commande.statut
        }, status=status.HTTP_200_OK)

    # Ajouter des produits à une commande
    @action(detail=True, methods=['post'], url_path='produits')
    def ajouter_produits(self, request, pk=None):
        commande = self.get_object()  # Récupère la commande par son ID
        produits_data = request.data.get('produits', [])  # Liste des produits envoyée dans la requête

        # Parcourir chaque produit dans la liste
        for produit_data in produits_data:
            produit_id = produit_data.get('produit_id')
            quantite = produit_data.get('quantite', 1)  # Quantité par défaut à 1 si non spécifiée

            try:
                produit = Pokedex.objects.get(id=produit_id)
            except Pokedex.DoesNotExist:
                return Response({'error': f'Produit avec id {produit_id} non trouvé'}, status=status.HTTP_404_NOT_FOUND)

            # Ajouter le produit à la commande avec la quantité spécifiée
            commande.produits.add(produit, through_defaults={'quantite': quantite})

        return Response({'message': 'Produits ajoutés à la commande avec succès'}, status=status.HTTP_200_OK)

# Vue pour gérer le Pokedex
class PokedexViewSet(viewsets.ModelViewSet):
    queryset = Pokedex.objects.all()
    serializer_class = PokedexSerializer

    # Endpoint pour vérifier les niveaux de stock
    @action(detail=False, methods=['get'], url_path='stock')
    def check_stock(self, request):
        produits_disponibles = Pokedex.objects.filter(quantite__gt=0)
        serializer = self.get_serializer(produits_disponibles, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    # Endpoint pour mettre à jour les niveaux de stock après commande
    @action(detail=True, methods=['post'], url_path='update-stock')
    def update_stock(self, request, pk=None):
        produit = self.get_object()
        quantite_commandee = request.data.get('quantite', 0)

        # Vérifie si la quantité est valide
        if produit.quantite >= int(quantite_commandee):
            produit.quantite -= int(quantite_commandee)
            produit.save()
            return Response({"message": "Stock mis à jour avec succès"}, status=status.HTTP_200_OK)
        else:
            return Response({"error": "Quantité insuffisante en stock"}, status=status.HTTP_400_BAD_REQUEST)
        

# Vue pour gérer les paiements
stripe.api_key = settings.STRIPE_SECRET_KEY

class PaiementView(APIView):
    def post(self, request, *args, **kwargs):
        commande_id = request.data.get('commande_id')
        montant = request.data.get('montant')

        try:
            commande = Commande.objects.get(id=commande_id)
        except Commande.DoesNotExist:
            return Response({"error": "Commande non trouvée"}, status=status.HTTP_404_NOT_FOUND)

        # Créer une session de paiement Stripe
        try:
            session = stripe.checkout.Session.create(
                payment_method_types=['card'],
                line_items=[{
                    'price_data': {
                        'currency': 'eur',
                        'product_data': {
                            'name': f"Commande {commande.numero_commande}",
                        },
                        'unit_amount': int(float(montant) * 100),  # Stripe attend le montant en centimes
                    },
                    'quantity': 1,
                }],
                mode='payment',
                success_url='https://example.com/success',  # URL de succès à personnaliser
                cancel_url='https://example.com/cancel',    # URL d'annulation
            )

            # Créer un paiement en base de données
            paiement = Paiement.objects.create(
                transaction_id=session.id,
                commande=commande,
                montant=montant,
                statut='en_attente',
            )
            # Retourner l'URL de la session Stripe dans la réponse
            return Response({
                'session_id': session.id,
                'paiement': PaiementSerializer(paiement).data,
                'stripe_url': session.url  # Lien direct vers la session Stripe
            }, status=status.HTTP_201_CREATED)

        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

class StatutPaiementView(APIView):
    def get(self, request, transaction_id):
        try:
            paiement = Paiement.objects.get(transaction_id=transaction_id)
        except Paiement.DoesNotExist:
            return Response({"error": "Paiement non trouvé"}, status=status.HTTP_404_NOT_FOUND)

        return Response({
            'transaction_id': paiement.transaction_id,
            'statut': paiement.statut,
            'montant': paiement.montant,
            'date_creation': paiement.date_creation
        })
    


class AvisViewSet(viewsets.ModelViewSet):
    queryset = Avis.objects.all()
    serializer_class = AvisSerializer

    # Afficher les avis d'un produit spécifique
    @action(detail=True, methods=['get'], url_path='avis')
    def afficher_avis(self, request, pk=None):
        produit = get_object_or_404(Pokedex, pk=pk)
        avis = Avis.objects.filter(produit=produit)
        serializer = self.get_serializer(avis, many=True)
        return Response(serializer.data)

    # Ajouter un avis pour un produit
    @action(detail=True, methods=['post'], url_path='ajouter-avis')
    def ajouter_avis(self, request, pk=None):
        produit = get_object_or_404(Pokedex, pk=pk)
        utilisateur = request.user  # Assurez-vous que l'utilisateur est authentifié

        # Vérifier si l'utilisateur a acheté le produit
        if not Commande.objects.filter(utilisateur=utilisateur, produits=produit).exists():
            return Response({"error": "Vous devez acheter ce produit pour laisser un avis"}, status=status.HTTP_403_FORBIDDEN)
        
        note = request.data.get('note')
        commentaire = request.data.get('commentaire')

        avis = Avis.objects.create(
            utilisateur=utilisateur,
            produit=produit,
            note=note,
            commentaire=commentaire
        )

        return Response({"message": "Avis ajouté avec succès", "avis": AvisSerializer(avis).data}, status=status.HTTP_201_CREATED)

    # Supprimer un avis (réservé aux administrateurs)
    @action(detail=True, methods=['delete'], url_path='supprimer-avis')
    def supprimer_avis(self, request, pk=None):
        avis = get_object_or_404(Avis, pk=pk)
        utilisateur = request.user
        if utilisateur.statut != 'admin':
            return Response({"error": "Vous n'êtes pas autorisé à supprimer cet avis"}, status=status.HTTP_403_FORBIDDEN)
        avis.delete()
        return Response({"message": "Avis supprimé avec succès"}, status=status.HTTP_204_NO_CONTENT)
    
class LoginView(APIView):
    def post(self, request):
        email = request.data.get('email')
        password = request.data.get('password')

        utilisateur = authenticate(request, email=email, password=password)

        if utilisateur is not None:
            refresh = RefreshToken.for_user(utilisateur)
            return Response({
                'refresh': str(refresh),
                'access': str(refresh.access_token),
            })
        else:
            return Response({"error": "Email ou mot de passe incorrect"}, status=status.HTTP_401_UNAUTHORIZED)