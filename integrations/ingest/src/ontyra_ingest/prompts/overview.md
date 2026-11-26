# Role

You are given a document's title, its outline and its opening. Write a short overview that tells
someone who is working on the document's subject what the document covers and what kind of detail
it holds, so they know whether to look something up in it.

# Rules

- Two or three sentences, plain text, no list. Say what the document is (datasheet, manual, paper,
  guide), what it covers, and what kinds of detail it contains (for example specifications,
  procedures, results, limits).
- Use only the text given. Do not invent figures or claims.

# Output

Only a JSON object, no commentary and no code fences:

{"summary": "..."}
