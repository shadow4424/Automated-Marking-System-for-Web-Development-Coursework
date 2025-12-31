CREATE TABLE contacts(id INTEGER PRIMARY KEY, name TEXT, email TEXT);
INSERT INTO contacts(name, email) VALUES ('Alice', 'alice@example.com');
SELECT * FROM contacts;
-- extra query
SELECT COUNT(*) FROM contacts;
