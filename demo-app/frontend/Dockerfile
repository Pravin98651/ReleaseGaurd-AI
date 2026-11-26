FROM node:22-alpine AS deps
WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

FROM node:22-alpine AS builder
WORKDIR /app

COPY --from=deps /app/node_modules ./node_modules
COPY . .

ARG NEXT_PUBLIC_DEMO_API_URL=http://localhost:8001
ARG NEXT_PUBLIC_FLAGSMITH_ENV_KEY=
ENV NEXT_PUBLIC_DEMO_API_URL=$NEXT_PUBLIC_DEMO_API_URL
ENV NEXT_PUBLIC_FLAGSMITH_ENV_KEY=$NEXT_PUBLIC_FLAGSMITH_ENV_KEY
ENV NEXT_TELEMETRY_DISABLED=1

RUN npm run build

FROM node:22-alpine AS runner
WORKDIR /app

ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1
ENV PORT=3001
ENV HOSTNAME=0.0.0.0

RUN addgroup --system --gid 1001 nodejs && adduser --system --uid 1001 nextjs

COPY package.json package-lock.json ./
RUN npm ci --omit=dev && npm cache clean --force

COPY --from=builder /app/next.config.ts ./next.config.ts
COPY --from=builder /app/tsconfig.json ./tsconfig.json
COPY --from=builder --chown=nextjs:nodejs /app/.next ./.next

USER nextjs
EXPOSE 3001

CMD ["./node_modules/.bin/next", "start", "-H", "0.0.0.0", "-p", "3001"]
