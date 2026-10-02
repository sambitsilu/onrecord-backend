# Deploying to Google Cloud (for later)

> **Don't run this yet.** It's here so the steps are ready when you decide to deploy.
> These commands create resources that cost money. Nothing in this folder runs them for you.

Target setup: Cloud Run (API) + Cloud SQL for PostgreSQL 16 + Secret Manager +
Identity Platform (sign-in), in **asia-south1 (Mumbai)** so client data stays in India.

Replace `PROJECT` with your project id. Check current prices in the Google Cloud pricing
calculator before starting; Cloud SQL is the main fixed cost.

## 1. Project and APIs

```bash
gcloud config set project PROJECT
gcloud services enable run.googleapis.com sqladmin.googleapis.com secretmanager.googleapis.com \
  artifactregistry.googleapis.com cloudbuild.googleapis.com identitytoolkit.googleapis.com
```

## 2. Database

```bash
gcloud sql instances create onrecord-db --database-version=POSTGRES_16 --region=asia-south1 \
  --edition=ENTERPRISE --tier=db-g1-small --backup-start-time=20:30 --enable-point-in-time-recovery
gcloud sql databases create onrecord --instance=onrecord-db
gcloud sql users create onrecord_owner --instance=onrecord-db --password='CHOOSE-A-LONG-PASSWORD'
```

`onrecord_owner` runs migrations only. The app gets a limited user in step 5.

## 3. Secrets

The Cloud SQL connection uses a Unix socket path:

```bash
printf %s 'postgresql+psycopg://onrecord_owner:OWNER-PASSWORD@/onrecord?host=/cloudsql/PROJECT:asia-south1:onrecord-db' \
  | gcloud secrets create db-owner-url --data-file=-
printf %s 'postgresql+psycopg://onrecord_app:APP-PASSWORD@/onrecord?host=/cloudsql/PROJECT:asia-south1:onrecord-db' \
  | gcloud secrets create db-app-url --data-file=-
printf %s 'YOUR-ANTHROPIC-API-KEY' | gcloud secrets create anthropic-api-key --data-file=-
printf %s 'YOUR-ASSEMBLYAI-API-KEY' | gcloud secrets create assemblyai-api-key --data-file=-
```

## 4. Build the image and run migrations

```bash
gcloud artifacts repositories create onrecord --repository-format=docker --location=asia-south1
gcloud builds submit --tag asia-south1-docker.pkg.dev/PROJECT/onrecord/api:v1

gcloud iam service-accounts create onrecord-api
for role in roles/cloudsql.client roles/secretmanager.secretAccessor; do
  gcloud projects add-iam-policy-binding PROJECT \
    --member=serviceAccount:onrecord-api@PROJECT.iam.gserviceaccount.com --role=$role
done

gcloud run jobs create onrecord-migrate --region=asia-south1 \
  --image=asia-south1-docker.pkg.dev/PROJECT/onrecord/api:v1 \
  --service-account=onrecord-api@PROJECT.iam.gserviceaccount.com \
  --set-cloudsql-instances=PROJECT:asia-south1:onrecord-db \
  --set-secrets=DATABASE_URL=db-owner-url:latest \
  --command=alembic --args=upgrade,head
gcloud run jobs execute onrecord-migrate --region=asia-south1 --wait
```

## 5. Limited database user for the app

After every release that adds migrations, re-run the GRANT lines of `sql/app_role.sql`.

Open **Cloud SQL Studio** for `onrecord-db` (or `gcloud sql connect onrecord-db --user=onrecord_owner --database=onrecord`),
change the password in `sql/app_role.sql` to match `APP-PASSWORD` from step 3, and run the file.
This user can't alter tables or switch off the activity-log protection.

## 6. Sign-in (Identity Platform)

In the console: **Identity Platform → Providers → Add provider → Google** (and Email/Password if you want it).
Add your web app's domain under **Settings → Authorized domains**. The web app signs users in with the
Firebase JS SDK and sends the ID token as `Authorization: Bearer <token>`.

## 7. Deploy the API

```bash
gcloud run deploy onrecord-api --region=asia-south1 \
  --image=asia-south1-docker.pkg.dev/PROJECT/onrecord/api:v1 \
  --service-account=onrecord-api@PROJECT.iam.gserviceaccount.com \
  --add-cloudsql-instances=PROJECT:asia-south1:onrecord-db \
  --set-secrets=DATABASE_URL=db-app-url:latest,ANTHROPIC_API_KEY=anthropic-api-key:latest,ASSEMBLYAI_API_KEY=assemblyai-api-key:latest \
  --set-env-vars=ENV=production,AUTH_MODE=firebase,FIREBASE_PROJECT_ID=PROJECT,LLM_MODEL=claude-sonnet-5-5,ALLOWED_ORIGINS=https://app.YOURDOMAIN,PUBLIC_BASE_URL=https://api.YOURDOMAIN,TRANSCRIPTION_PROVIDER=assemblyai \
  --no-cpu-throttling --timeout=900 --min-instances=0 --max-instances=3 \
  --allow-unauthenticated
```

- `--allow-unauthenticated` lets browsers reach the API; the app itself checks every sign-in.
- `--no-cpu-throttling` keeps background call-reading running after the response is sent
  (billed per instance time). For heavy use, move background work to Cloud Tasks.
- Check it: `curl https://<service-url>/healthz` and `/readyz`.

## 8. After deploying

- Run the eval against the deployed model: `ANTHROPIC_API_KEY=... python -m eval.run_eval --repeat 3`.
- Set a budget alert in Billing.
- Turn on Cloud SQL deletion protection: `gcloud sql instances patch onrecord-db --deletion-protection`.
- Write down your backup retention period for the privacy policy.

## Updating

Build a new tag, run the migrate job with the new image if the release has migrations, then
`gcloud run deploy onrecord-api --image=…:v2`. Roll back by deploying the previous tag.
