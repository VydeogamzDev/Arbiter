import { Express } from 'express';
import { Config } from '../config';
import { requireUser } from './middleware';
import * as notes from './notesHandlers';
import * as accounts from './accountHandlers';

export function registerRoutes(app: Express, config: Config) {
  app.post('/signup', accounts.signup);
  app.post('/login', accounts.login(config));
  app.get('/notes', requireUser(config), notes.list);
  app.post('/notes', requireUser(config), notes.create(config));
  app.post('/notes/:id/share', requireUser(config), notes.share);
  app.get('/search', requireUser(config), notes.search);
}
