# fireside

fireside turns a book or a story link into finished chapter audio that a person can play, pause, resume and keep, on their own server or on the hosted site.

## Library

**Listener**:
A person who has books read aloud to them by fireside.
_Avoid_: reader (for the person), user (in product text)

**Library**:
Every book a user holds.
_Avoid_: shelf (for the whole library), collection

**Shelf**:
A container a user makes to organize books. Shelves nest inside shelves, and a book can sit on many shelves at once.
_Avoid_: container, collection, stacks, section

**Tag**:
A short label on a book, either declared by the book's own file or typed by the user. Tags are separate from shelves.

**Book**:
One text a user holds in their library, made of ordered chapters. Re-importing identical text is the same book.
_Avoid_: ebook (as the entity), title, story, work

**Part**:
One of several separately published pieces (such as posts) that together import as one book.
_Avoid_: post, installment

**Chapter**:
One ordered section of a book, and the unit that plays as one audio file.

**Author**:
The person credited on a book. Different spellings of one name ("B. Arthur", "Arthur, B.") are one author.

**Series**:
An ordered group of books that belong together, from the book's own metadata or set by the user. A series is not a shelf: it has an order, it comes with the books, and it is a pronunciation scope.
_Avoid_: series shelf, series container

## Import

**Reader**:
A plugin that turns a file or a link into a book (EPUB, DOCX, PDF, a web page, AO3). Never the person.
_Avoid_: source, importer, parser

**Import**:
Bringing a file or a story link into the library as a book.

**Draft**:
An extracted but not yet imported book, shown in the preview so the user can edit it before it becomes a book.

**Voicing**:
Turning a book's text into chapter audio with a chosen voice. A book is **unvoiced** until the user chooses to voice it.
_Avoid_: render, convert, conversion, narrate, narration, encode

**Already voiced**:
A book whose identical text has already been voiced in the same voice with the same custom pronunciation rules, so voicing it again costs nothing. Which connection reached the voice does not matter.

**Flag**:
A machine suggestion that a piece of draft text is junk (running header, page number, front matter). A flag is an offer; text is only dropped when the user accepts it.

## Pronunciation

**Pronunciation rule**:
A word as it appears in the text, paired with a respelling of how it should sound.
_Avoid_: grammar rule, fix

**Model review**:
An optional pass before voicing in which a model looks for words with more than one reading ("live", "wind") and asks the user about the ones it cannot decide. Voicing starts only after the whole review is done.

**Reading**:
The user's answer to one model-review question: how one word sounds in one sentence. A reading is one-off and becomes a pronunciation rule only if the user saves it as one.
_Avoid_: rule, disambiguation

**Dictionary**:
A named set of pronunciation rules attached at one scope.

**Scope**:
Where a dictionary applies: Book, Series, Author or Global. The narrowest scope wins (Book > Series > Author > Global). A book whose author or series is unknown falls into the **Unknown** scope.

## Voices and connections

**Speech provider**:
An external or self-hosted text-to-speech service (Azure, MiniMax, an OpenAI-compatible server such as Kokoro).
_Avoid_: engine, TTS service

**Voice**:
One named speaker offered by a speech provider.

**Connection**:
One configured way to reach a speech provider, with its credentials. A **system connection** is set up by an admin for every user; a **personal connection** belongs to one user.
_Avoid_: voice service, provider key

**Connection order**:
The listener's own order for their connections. It only sorts the voice picker; the voice the listener picks decides which connection is used.
_Avoid_: precedence, fallback order

**Sale connection**:
The one connection whose use is paid for out of the user's balance.

**Pass-through connection**:
Any connection other than the sale connection. fireside never charges for its use.
_Avoid_: BYO connection, free connection

**Chunk**:
The part of a chapter sent to a speech provider in one request, and the smallest unit that is re-voiced when a pronunciation rule changes.
_Avoid_: piece, segment

## Money

**Balance**:
The money a user has in fireside to pay for voicing on the sale connection, in dollars. It rolls over, never expires and is never refunded.
_Avoid_: characters, character balance, credits (as the balance)

**Credit**:
The monthly dollar amount a plan adds to the balance.
_Avoid_: allowance, included characters

**Funds**:
Money a user adds to their balance directly, outside their plan.
_Avoid_: top-up, credit pack

**Quote**:
The estimated cost of voicing something, given before it starts.

**Reservation**:
The estimated amount held from the balance while voicing runs, so concurrent requests cannot overspend.
_Avoid_: hold

**Settlement**:
Replacing a reservation with the actual cost once the provider reports it.

**Receipt**:
What the user sees after settlement: the estimate, the actual, the platform cost and the platform fee.
_Avoid_: invoice, bill

**Plan**:
What an account subscribes to: **free** or **platform**.
_Avoid_: tier

**Storage block**:
An add-on bought on top of a plan that adds a fixed amount of storage. It is not a plan.

**Storage allowance**:
How much a user may store: their plan's storage plus any storage blocks. Every book a user holds counts against it in full, even if another user holds the same book.

**Grant**:
Anything a listener receives without paying for it: a plan's monthly credit, a comp, or an unlimited switch.
_Avoid_: gift, bonus, free credit

**Comp**:
A plan given by an admin rather than paid for. A kind of grant.

**Unlimited switch**:
A grant from an admin that lifts one limit (storage or credit) for one listener. Usage is still measured; only the refusal is lifted.
_Avoid_: unlimited plan, admin grant

## Playback

**Position**:
The one place a user is in a book: a chapter and an offset. One per user per book.
_Avoid_: bookmark

**Owner device**:
The single device currently allowed to play a book for a user.

**Handoff**:
Another device claiming ownership by pressing play, which stops the previous owner immediately.

**Offline copy**:
Every already-voiced chapter of a book pulled to a device. Making one never triggers voicing.

## Deployment

**Household**:
One fireside server shared by a few accounts.

**Hosted**:
The public fireside site at readfireside.com, with sign-ups and payments.

**Admin**:
The account that manages a server: the first account created on it.
