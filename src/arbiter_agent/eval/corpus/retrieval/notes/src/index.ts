import { buildServer } from './server';

buildServer().listen(Number(process.env.PORT ?? 3000));
