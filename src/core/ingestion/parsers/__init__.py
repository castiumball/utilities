"""
Parsers Package

Contains document parser implementations.

All parsers in this package and its subpackages are automatically
discovered and registered when the ingestion.factory module is imported.

To add a new parser:
    1. Create a new module in this package (or a subpackage)
    2. Define a class that inherits from Parser
    3. Decorate it with @register_parser(<name_of_parser>)
The parser will then be available via ParserFactory.get_parser(<name_of_parser>)
"""
