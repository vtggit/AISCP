# AIERP guidance register

These files hold the product vision as keyed items, one file per section of
`docs/PRODUCT-VISION.md`. The format is the build engine's guidance register (CodeAgent#1065):
each item has a permanent key, a type, a title, one statement, a record of who decided it, and
the authority needed to change it.

**Builds are still steered by `docs/PRODUCT-VISION.md`.** The engine's auto-answerer can read
this register instead (CodeAgent#1069, switched on per product), for an issue that names its
area. A change to the vision is therefore made in both places.

## How the files were made

They were converted from the document as it stood on 2026-11-11, wording unchanged:

- every top-level bullet is an item; a bullet whose sub-bullets are sentences of their own is one item
  per sub-bullet, and a list of fragments stays one item; a paragraph that gives context is the area's `summary`;
- the type comes from the section: the north star and "what AIERP is not" are `direction`, the
  wedge's capabilities are `roadmap`, the hard boundaries are `boundary`, everything else is
  `design`;
- the decision record comes from the document itself. Who decided: the heading's own note
  ("operator decision", or "decided by Claude under operator delegation") or a note inside the
  bullet. When: that note's date, else the date of the commit in which the words first
  appeared. `ref` names that pull request;
- what the operator decided in person needs the executive level to change; a design item a
  delegate decided keeps the type's default.

Every line of the document is carried by an item or a summary; a script checked it word for
word. Keys were assigned once, in document order, and are never reused.

## How they are used

- An issue names the areas it belongs to with `[area: name]` and the items it rests on with
  `[guidance: KEY, KEY]`.
- An agent is given the areas that apply to every issue (below), the issue's own areas, and a
  one-line index of the rest, instead of the whole document.
- A decision that rests on an item carries its stamp, `KEY@hash`: the key and a hash of the
  item's words, so it can be traced to the exact words even after the item changes.
- AIERP itself is given this register and shows it (the Guidance screen issues, from
  AIERP#1124).

## Adding guidance after the conversion

A new section is written in both places by hand: the section in `docs/PRODUCT-VISION.md` and one
new area file here. New items take the next free key of their type. A key is never reused and an
existing key is never renumbered, so do not run the converter again over the whole document.

## Areas that apply to every issue

Six areas carry `applies_to: {always: true}`: the north star, what AIERP is not, the standing
decisions, settings (system and personal), the method for deciding when nothing is recorded, and
the hard boundaries. Every agent that is given a slice of this register is given these areas
too, whatever the issue is about. Every other area is given only to an issue that names it, with
an `[area: name]` marker.
