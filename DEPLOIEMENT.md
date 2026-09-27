# Mettre le CRM en ligne (Render)

Temps : environ 15 minutes. Coût : environ 7,50 $ par mois
(offre « Starter » à 7 $ + disque de 1 Go pour garder les données).
L'offre gratuite de Render ne convient pas : les données y seraient effacées
à chaque redémarrage.

## 1. Créer le compte Render

1. Allez sur **https://render.com** → **Get Started**.
2. Choisissez **Sign up with GitHub** et utilisez le compte GitHub qui contient le dépôt `crm`.
3. Ajoutez un moyen de paiement (menu **Billing**).

## 2. Lancer l'application

1. Dans le tableau de bord Render : bouton **New +** → **Blueprint**.
2. Si le dépôt `younes557-a/crm` n'apparaît pas : **Configure GitHub** →
   autorisez l'accès au dépôt `crm`.
3. Sélectionnez `younes557-a/crm`, puis la branche
   `claude/electoral-crm-system-v7z3c4`.
4. Render lit le fichier `render.yaml` et affiche le service **crm-citoyens**.
   Donnez un nom au blueprint (ex. « CRM ») puis cliquez **Deploy Blueprint** (ou **Apply**).
5. Patientez 5 à 10 minutes (première installation). Quand l'état passe à
   **Live**, votre adresse s'affiche en haut de la page, du type
   **https://crm-citoyens.onrender.com**.

## 3. Première connexion

1. Ouvrez l'adresse : l'application vous demande de créer le **compte administrateur**.
   Choisissez un mot de passe solide.
2. Menu **Administration** : créez les comptes de l'équipe, l'élection en cours,
   importez votre fichier Excel de contacts et reliez la boîte mail.
3. Partagez l'adresse avec l'équipe. Sur téléphone : ouvrez-la dans le navigateur,
   puis « Ajouter à l'écran d'accueil » pour l'avoir comme une application.

⚠️ Faites l'étape 3.1 tout de suite après la mise en ligne : tant que le compte
administrateur n'existe pas, la première personne qui ouvre l'adresse peut le créer.

## Au quotidien

- **Mises à jour** : chaque modification poussée sur la branche est mise en ligne
  automatiquement par Render.
- **Sauvegardes** : Render fait un instantané quotidien du disque (onglet **Disks**
  du service). Pensez aussi à faire régulièrement un **Export Excel** depuis
  l'Administration.
- **E-mails** : cliquez sur **Synchroniser** dans la page Messagerie pour importer
  les nouveaux messages.
- **Nom de domaine** (ex. `crm.mon-equipe.fr`) : onglet **Settings → Custom Domains**.

## En cas de problème

- Onglet **Logs** du service : messages d'erreur.
- « Deploy failed » : onglet **Events** → relancez avec **Manual Deploy → Deploy latest commit**.
