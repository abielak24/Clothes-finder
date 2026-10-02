# fitfind

Which size to buy, product by product, from your own measurements.

If you're always between a medium and a large, the answer is different at
every brand. `fitfind` reads each product's own size chart, or the
"model is 6'1" and wears M" line under its photos, and compares it with
your measurements. Then it tells you which size to buy and which number
decided it.

```console
$ fitfind rank
Best fits for a regular fit

  Oxford Shirt (acme.example)                   L     1.00  high      garment chart: chest +0.5 in, length +0.5 in vs your reference shirt
  Everyday Tee (northway.example)               M     1.00  medium    body chart: chest spot on vs your body
  Linen Shirt (harbor.example)                  M     1.00  low       model notes: chest spot on vs the model (Model is 6'1", 40 in chest, wears M)
```

The same person is an L in one brand's oxford and an M in another's tee.

## Install

```console
git clone https://github.com/abielak24/clothes-finder && cd clothes-finder
python -m venv .venv && source .venv/bin/activate
pip install -e .
export ANTHROPIC_API_KEY=...      # used to read size charts out of product pages
```

## Use

**1. Measure once.** The best input is a shirt that already fits you well,
laid flat on a table:

```console
fitfind profile set --shirt-pit 21 --shirt-length 29.5 --shirt-shoulder 18
```

| Flag | How to measure |
| --- | --- |
| `--shirt-pit` | Armpit to armpit across the front. Doubled to give the chest. |
| `--shirt-length` | Top of the collar seam to the hem, at the back. |
| `--shirt-shoulder` | Shoulder seam to shoulder seam. |
| `--shirt-sleeve` | Shoulder seam to cuff. |

Add body measurements with a soft tape too, because some brands only publish
body charts:

```console
fitfind profile set --chest 39 --waist 33 --height "5'11\""
fitfind profile set --fit slim            # slim, regular (default) or relaxed
```

Numbers are in inches unless you pass `--unit cm`. A height can be written
as `5'11"` or `180cm`.

**2. List the brands you shop**, one store per line:

```console
cp brands.example.txt brands.txt
```

**3. Collect, then rank:**

```console
fitfind collect              # reads every shirt from every brand in brands.txt
fitfind rank                 # best fits first
fitfind rank oxford --top 5  # only titles containing "oxford"
```

**Or check one product** you're looking at:

```console
fitfind check https://some-brand.example/products/linen-shirt
```

This prints the size to buy and how every size scored:

```
  Buy L   score 1.00, confidence high
  garment chart: chest +0.5 in, length +0.5 in vs your reference shirt

    S      0.00     chest -4.0, length -1.5
    M      0.22     chest -1.5, length -0.5
    L      1.00  <- chest +0.5, length +0.5
    XL     0.00     chest +3.0, length +1.5
```

**4. Tell it how things fit.** After a shirt arrives:

```console
fitfind owned https://some-brand.example/products/linen-shirt --size M --fit tight
fitfind owned                 # list what you've recorded, and each brand's adjustment
```

A brand whose shirts have run tight on you shifts that brand's
recommendations by about half a size.

## How a size is chosen

Each product's evidence is used in this order. Only one is used per
product, and the output says which:

| Evidence | Compared with | Confidence |
| --- | --- | --- |
| Garment chart (the shirt's own measurements) | Your reference shirt | high |
| Garment chart | Your body plus ease for your fit | medium |
| Body chart ("fits chest 38–40 in") | Your body | medium |
| Model notes ("6'1", 40 in chest, wears M") | Your body, one size step ≈ 2 in of chest | low |
| The brand's site-wide size chart | Either of the above | very low |

For each measurement, a difference of 0 scores 1, and the score falls to 0
at a tolerance. Garment differences within half an inch count as exact,
because shirts of one size vary by about that much. Tolerances are 1.5 in
for chest and length, 1 in for shoulder and sleeve on garments, and 2 in
for chest on body charts. A size that is too tight
reaches 0 sooner than one the same amount too loose. By default it's 1.5
times sooner (`--tight-penalty`). With stretch fabric the two count
equally. Chest carries the most weight, and your `--priority` measurement
counts double.

When two adjacent sizes score within 0.05 of each other, the result is
shown as `M/L`. An exact tie goes to the smaller size for a slim fit and
the larger for a relaxed one. For a regular fit it goes to the size whose
range you sit nearest the middle of.

`rank` sorts by score, discounted by confidence. A guess from model notes
doesn't outrank a product measured against your own shirt.

## How products are collected

For each store in `brands.txt`:

1. **Shopify feed.** Many direct-to-consumer brands run on Shopify, which
   publishes `/products.json`. The feed includes each product's sizes and
   description HTML, which is usually where size charts and model notes
   live.
2. **Sitemap.** For other stores, product pages whose URLs look like
   shirts are found in `sitemap.xml` and fetched one at a time.
3. **Size-chart page.** If a product links to a separate size guide on the
   same site, that page is read once and kept as the brand's site-wide
   chart.

Claude (`claude-opus-5-5` by default; set `FITFIND_MODEL` to change it)
reads each page's text into a fixed schema: charts, model notes, fit name
and stretch. It reports numbers in the page's own units, and the code
converts them. Pages with no sizing words aren't sent at all, and a product
is read only once unless you pass `--reextract`. If the page doesn't say
whether a chart is body or garment, the numbers decide. A medium chest of
42 in or more, or a chart listing length or shoulder, means garment.

The collector sends at most one request per second to each store, obeys
`robots.txt`, and keeps every response in `~/.fitfind/fitfind.db`. A page
is fetched again only after 14 days (`--max-age`). It's meant for personal
use. Don't republish what it collects.

## Limits

- Body sleeve (centre back to wrist) and garment sleeve (shoulder seam to
  cuff) are measured differently, so the two are never compared.
- Model notes assume the model wears the brand's intended fit, and that
  adjacent sizes are about 2 in apart at the chest.
- Stores that block automated requests, or render sizing only with
  JavaScript, yield no data. They show up in `collect` as products without
  sizing.
- Only tops for now. Pants would use the same approach, with waist, inseam
  and rise.

## Develop

```console
pip install -e ".[dev]"
pytest
```

The tests use fake HTTP responses and a fake Claude client, so they need
no network or API key.
