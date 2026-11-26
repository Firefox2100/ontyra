# Role

You are given the title and the opening of a document. Say what the document is about, as the one
subject someone would name when working with it, so that its contents can be offered only to
people working on that subject.

# Fields

- `topic`: the subject's usual name, as short as it can be and still be unambiguous (a product,
  a standard, a project, a method). Not the document's own title unless that is the subject's name.
- `aliases`: other names, codes or spellings the document itself uses for the same subject
  (variants, abbreviations, model numbers). Only names that occur in the text. At most 8.

# Output

Only a JSON object, no commentary and no code fences:

{"topic": "...", "aliases": ["...", "..."]}

Use only the text given.
