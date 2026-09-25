import { issueToken, verifyToken } from '../src/auth/tokens';

test('roundtrip', () => expect(verifyToken(issueToken(1, 's'), 's')).toEqual({ id: 1 }));
