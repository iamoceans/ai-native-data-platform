FROM node:24.21.0-alpine AS build

WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY tsconfig.json vite.config.ts index.html openapi.json ./
COPY src ./src
# schema.d.ts is generated from the committed OpenAPI document so a fresh clone
# builds without running the backend first.
RUN npm run gen:api && npm run build

FROM nginx:1.29.8-alpine

RUN rm /etc/nginx/conf.d/default.conf
COPY nginx.conf /etc/nginx/conf.d/ainative.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80
