import { normalizeTags } from '../src/notes/tags';

test('dedupes', () => expect(normalizeTags(['A', 'a '])).toEqual(['a']));
