# Changelog

## 0.9.0-beta.16

- Seksten nye diagnosesensorer viser eftervarmens model, forudsigelse og ydelse (kræver Pi 1.13.0-beta.11): styringsmetode, modelstatus, forudsagt luft om 1 og 10 minutter, laveste forudsagte luft, forudsigelsesfejl, varme pr. trin, reaktionstid op og ned, forsinkelse, rettelse, effekt (kW), energi i dag (kWh, kan bruges i energioversigten) samt tid under bund, middelafvigelse og skift pr. time over 24 timer.
- Ældre Pi-versioner sender ikke tallene: så oprettes sensorerne ikke. En sensor uden værdi vises som utilgængelig.
- Kun aflæsning; styringen er uændret. Genstart Home Assistant efter installationen. Ændret af Claude AI.

Se [release notes](docs/releases/0.9.0-beta.16.md).

## 0.9.0-beta.15

- Send for hvert rum feltet `missing_sensors` med de målingstyper (`temperature`, `humidity`, `co2`, `pm25`), hvor en valgt sensor er utilgængelig, ukendt, ikke-numerisk, uden for området eller fjernet fra HA. Typer uden valgt sensor tælles ikke med, og feltet udelades, når intet mangler.
- Vis manglende valgte sensorer på **Smart Auto input online** (attributten `manglende_sensorer`) og i diagnosticeringen.
- Pi 1.13.0-beta.5 viser advarslen; ældre Pi-versioner ignorerer feltet. Rumbeskeden er ellers uændret. Genstart Home Assistant efter installationen.

Se [release notes](docs/releases/0.9.0-beta.15.md).

## 0.9.0-beta.14

- Slå hovedenheden og Pi-controlleren op med Home Assistant's nye `async_get_device_by_identifier` inden for integrationens egen post. Det udfasede `async_get_device` (fjernes i HA 2027.8) bruges kun på HA før 2026.8.
- Identifikatorer, enhedsnavne og entity-ID'er bevares. Ingen ny Pi-version kræves. Genstart Home Assistant efter installationen.

Se [release notes](docs/releases/0.9.0-beta.14.md).

## 0.9.0-beta.13

- Send alderen på hver temperatur-, fugt-, CO₂- og PM2.5-måling fra rummene. Alderen bygger på seneste HA-rapport, og gentagen videresendelse nulstiller den ikke.
- Markér den nye aldersprotokol, så Pi 1.13.0-beta.1 kan udelade enkelte for gamle målinger og vise deres kvalitet. Ugyldige eller fremtidige tidsstempler bliver ukendte.
- Bevar kompatibilitet med ældre Pi-versioner. Entity-ID'er, rumopsætning og controllerindstillinger bevares.
- Genstart Home Assistant efter installationen. Den eksisterende indirekte HAC1-eftervarmestyring bevares; direkte 0–10 V-styring er ikke en del af opdateringen.

Se [samlede release notes, versionskrav og validering](docs/releases/0.9.0-beta.13.md).
