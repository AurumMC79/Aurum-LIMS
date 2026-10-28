"""Business logic. Every function takes an open SQLAlchemy session; the caller
owns the transaction (see ``Database.session``). Functions that act on behalf
of a user take a ``SessionUser`` and check that user's permissions themselves,
so the rules hold no matter which screen calls them."""
