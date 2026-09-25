CREATE TABLE accounts (email TEXT PRIMARY KEY, salt BLOB, hash BLOB);
CREATE TABLE notes (id INTEGER PRIMARY KEY, owner_id INTEGER, title TEXT, body TEXT);
CREATE TABLE collaborators (note_id INTEGER, email TEXT);
