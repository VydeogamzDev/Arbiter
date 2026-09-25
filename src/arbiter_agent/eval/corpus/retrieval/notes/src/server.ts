import express from 'express';
import { registerRoutes } from './http/routes';
import { loadConfig } from './config';

export function buildServer() {
  const app = express();
  const config = loadConfig();
  registerRoutes(app, config);
  return app;
}
