# Holdet — Decision Support Frontend

Next.js 16 + Tailwind + Supabase + recharts.

## Development

```bash
cd frontend
npm install
npm run dev
```

Open [http://localhost:3000](http://localhost:3000).

Copy `.env.local.example` to `.env.local` and fill in the values.

## Deployment (Netlify)

1. In Netlify: **Add new site → Import from GitHub** → select `lstol/Holdet`
2. Set **base directory**: `frontend`
3. **Build command**: `npm run build`
4. **Publish directory**: `frontend/.next`
5. Add environment variables:
   - `NEXT_PUBLIC_SUPABASE_URL` — from Supabase project settings
   - `NEXT_PUBLIC_SUPABASE_ANON_KEY` — from Supabase project settings
   - `ANTHROPIC_API_KEY` — for Gather Intelligence feature
6. Deploy site
7. In Netlify domain settings: add custom domain `holdet.syndikatet.eu`
   Then add a CNAME record at your domain registrar pointing to your Netlify URL.

## GitHub Actions

Set these repository secrets for the keep-alive cron:
- `SUPABASE_URL`
- `SUPABASE_SERVICE_KEY`
