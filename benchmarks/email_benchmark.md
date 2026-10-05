# Email extractor benchmark: v3 vs v2

Built-in test sites: 20 (offline copies of common small-business site patterns).

| site | correct answer | v3 found | v3 | v2 best (confidence) | v2 |
|---|---|---|---|---|---|
| joesplumbing.com | info@joesplumbing.com | info@joesplumbing.com | ✅ | info@joesplumbing.com (83) | ✅ |
| acme-roofing.com | estimates@acme-roofing.com | - | ❌ | estimates@acme-roofing.com (100) | ✅ |
| greenlawn.com | hello@greenlawn.com | - | ❌ | hello@greenlawn.com (98) | ✅ |
| bestdumpsters.com | rentals@bestdumpsters.com | rentals@bestdumpsters.com | ✅ | rentals@bestdumpsters.com (91) | ✅ |
| cityhvac.com | service@cityhvac.com | - | ❌ | service@cityhvac.com (81) | ✅ |
| oldsite-pest.com | office@oldsite-pest.com | design@webguys.net | ❌ | office@oldsite-pest.com (92) | ✅ |
| tree-masters.com | treemasters.tx@gmail.com | treemasters.tx@gmail.com | ✅ | treemasters.tx@gmail.com (72) | ✅ |
| poolpros.com | poolprosdallas@gmail.com | - | ❌ | poolprosdallas@gmail.com (56) | ✅ |
| smithelectric.com | john@smithelectric.com | - | ❌ | john@smithelectric.com (100) | ✅ |
| sentry-site.com | hello@sentry-site.com | - | ❌ | hello@sentry-site.com (92) | ✅ |
| js-email.com | info@js-email.com | - | ❌ | info@js-email.com (70) | ✅ |
| privacy-only.com | privacy@privacy-only.com | - | ❌ | privacy@privacy-only.com (77) | ✅ |
| redirect-site.com | info@newbrand.com | - | ❌ | info@newbrand.com (100) | ✅ |
| noemail-carpet.com | (none) | - | ✅ | - (0) | ✅ |
| ssl-broken.com | sales@ssl-broken.com | sales@ssl-broken.com | ✅ | sales@ssl-broken.com (83) | ✅ |
| team-page.com | mike.jones@team-page.com | - | ❌ | mike.jones@team-page.com (93) | ✅ |
| entity-encoded.com | info@entity-encoded.com | - | ❌ | info@entity-encoded.com (83) | ✅ |
| wix-site.com | bookings@wix-site.com | - | ❌ | bookings@wix-site.com (100) | ✅ |
| data-attr.com | crew@data-attr.com | crew@data-attr.com | ✅ | crew@data-attr.com (76) | ✅ |
| reach-hvac.com | dispatch@reach-hvac.com | - | ❌ | dispatch@reach-hvac.com (92) | ✅ |

**v3: 6/20 correct, 1 wrong-company/junk picks.**  
**v2: 20/20 correct, 0 wrong-company/junk picks.**  
Time: v3 0.03s, v2 0.07s (no network latency in this mode; v3 sleeps disabled).

v2 verification was off here (fake domains have no MX); guesses are never counted as answers.
