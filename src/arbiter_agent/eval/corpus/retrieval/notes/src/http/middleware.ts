import { verifyToken } from '../auth/tokens';
import { Config } from '../config';

export function requireUser(config: Config) {
  return (req: any, res: any, next: any) => {
    const user = verifyToken(req.headers.authorization ?? '', config.jwtSecret);
    if (!user) return res.status(401).end();
    req.user = user;
    next();
  };
}
