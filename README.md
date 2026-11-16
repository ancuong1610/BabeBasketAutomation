# babe basket automation

Daily pipeline: find products at German drugstores, draft posts, approve in Discord, publish.

## Step 1: dm scraper

```sh
python3 -m scraper.dm --category "Haarkur & Haarmaske" --out data/
python3 -m unittest          # tests, no network
```

Writes `data/dm_<category>_<date>.csv` with: store, dan, gtin, brand, name, price_eur,
unit_price, rating, review_count, categories, image_url (1200px), product_url, scraped_at.

Python 3.10+ standard library only, no installs. `--category` takes any category name shown
in dm.de's filters (e.g. "Haarpflege", "Handcreme", "Körperpflege").

### Where the data comes from

| Data | Endpoint |
|---|---|
| Category listing (price, rating, review count, images) | `product-search.services.dmtech.com/de/search/crawl?categoryNames=…` |
| Product detail (claims, ingredients, usage, Payback points) | `products.dm.de/product/products/detail/DE/dan/{dan}` |
| Ratings, batched | `stars.services.dmtech.com/api/DE/v3/ratings/{dan,dan,…}/summary` |
| Review texts | `stars.services.dmtech.com/api/DE/v3/reviews/{dan}` |

These are the JSON endpoints dm.de's own pages call; dm.de product pages contain no product
JSON-LD. robots.txt allows this. One category is one request; keep runs slow and once a day.
Crawl approach after [badlogic/heissepreise](https://github.com/badlogic/heissepreise) (MIT).

Rossmann: its robots.txt blocks Claude's crawlers, so it is not covered here yet.
