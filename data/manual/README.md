# Manual snapshots

Files here were queried by hand because their publishers' servers are not reachable from the
build machine (Wikidata Query Service, Wikipedia, USGS). Each file says where and when it came
from in its `_about` and `query` keys. The pipeline's `sources/` modules read these files the
same way they read a cached download, and the Almanac cites them.
