import { registerAccount, authenticate } from '../auth/accounts';
import { issueToken } from '../auth/tokens';
import { Config } from '../config';

export const signup = (req: any, res: any) => res.json(registerAccount(req.body.email, req.body.password));
export const login = (config: Config) => (req: any, res: any) => {
  const account = authenticate(req.body.email, req.body.password);
  return account ? res.json({ token: issueToken(account.id, config.jwtSecret) }) : res.status(401).end();
};
