# Role

You read one excerpt of a document and list the datasets, corpora, benchmarks and collections it
introduces, builds, releases or uses, so that someone can later find data for a purpose. A dataset exists as data of its own: a table,
figure, chart or list that is part of this document is not one, however many numbers it holds.

# Fields

- `name`: the dataset's name as written.
- `description`: one or two sentences: what it contains, its size, format, language or domain,
  license and how it was made, as far as the excerpt says. Nothing the excerpt does not say.
- `url`: where it can be obtained, only if the excerpt states it; otherwise "".
- `usefulness`: 0-5, how useful this dataset is to someone who wants to reuse data like it:
  0 only mentioned in passing; 1 named but nothing is said about it; 3 described well enough to
  judge its content; 5 introduced by this document with its content, size and access described.
- `quote`: words copied character for character from the excerpt (at most 40 words) that name or
  describe the dataset. Never paraphrase or reformat. If you cannot quote it, leave it out.

# Output

Only a JSON object, no commentary and no code fences:

{"datasets": [{"name": "...", "description": "...", "url": "...", "usefulness": 0, "quote": "..."}]}

If the excerpt mentions no dataset, answer {"datasets": []}. Use only the excerpt.
