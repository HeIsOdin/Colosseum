# Harena

Harena is the React frontend for Hypogeum. The name means arena sand, and this app is the player-facing arena where series are listed, joined, and entered.

## Stack

- React + TypeScript
- Vite
- React Router
- TanStack Query
- Zod for API response validation
- Radix Dialog for the challenge interaction drawer
- Custom CSS/SVG visual system

## Current visual direction

The homepage follows an event-list layout inspired by capture-event dashboards:

- dark Roman brown/black theme
- bronze/gold borders and action buttons
- top tabs for Ongoing, Upcoming, Joined, and Past
- search over series title and description
- horizontal series cards with image backgrounds when available
- minimal dark bronze gradient fallback when a series has no image

## Local development

From this directory:

```bash
npm install
npm run dev
```

The Vite development server proxies these backend paths to `http://localhost:5000`:

```text
/auth
/series
/players
/diagnostics
```

If your Flask backend runs somewhere else, set:

```bash
VITE_API_BASE_URL=http://localhost:5000
```

or update `vite.config.ts`.

## Auth expectations

The frontend supports the current backend auth shape:

- `POST /auth/` may return `pid`, `sids`, and `is_admin` at the top level.
- `GET /auth/` may return those values under `details`.

The API client normalizes both shapes.

## Campaign model

This first pass keeps campaign modules local. `src/campaigns.tsx` maps series content to a campaign skin. Biafra receives an archival dossier tone while the Hypogeum shell owns auth, routing, API calls, challenge interaction, and admin forms.
