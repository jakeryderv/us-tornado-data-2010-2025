# Kaggle listing and notebook maintenance

The v2.2.1 release includes 17 linked analysis tables and two ML views. The shared
dataset card contains both platform examples; the staged Kaggle listing renders
only the Kaggle quick start. `metadata.json` supplies current source notes and
file/column descriptions. `dataset-cover-image.png` labels the expanded scope.

Use `docs/RELEASING.md` for staging/publication. Export current remote metadata
before updating so unrelated settings are preserved, then apply the staged Kaggle
card, source notes, frequency, resources and cover. Check the saved remote values,
including nested `analysis/` and `ml/` file descriptions. Older API versions have
not always saved those nested descriptions; verify the listing rather than just
the success response. A missing UI description must be reported, not silently
assumed applied. Fixed-snapshot update frequency is `never`.

For `datasets metadata --update`, preserve the exported display license
`U.S. Government Works`; the creation/version metadata slug
`US-Government-Works` is rejected by this update endpoint. Apply changes to an
exported metadata copy and retain its license/settings. Collection methodology
is a separate UI field; keep it aligned with `collection-methodology.md`.

The notebook uses `kagglehub` and a pinned dataset attachment. It loads the small
ML views, predictor dictionary, backbone tornado table, coverage table and schema inventory; it inventories all 19 tables without eagerly loading large radar/warning
source tables. It demonstrates target selection, missingness and grouped model
inputs, not a fitted model or operational forecast.

After dataset publication, update `notebooks/kaggle_getting_started.ipynb` with the
actual dataset version and trusted release manifest hash. `kernel-metadata.json`
uses `owner/slug/N`; `kagglehub` uses `owner/slug/versions/N`. Execute locally, then:

```sh
uv run --group publish kaggle kernels push -p release/kaggle
uv run --group publish kaggle kernels status jakevanslyke/us-tornado-data-getting-started
```

The hosted notebook uses CPU, internet disabled and the pinned attached dataset.
Retrieve and verify the hosted output and source after completion. Store the run
receipt in `notebook-v2.2.1.json`; older receipts and notebook history are retained.
A notebook-only update does not create a new dataset version.
