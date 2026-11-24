# Changelog

## 0.9.0-beta.13

- Send alderen på hver temperatur-, fugt-, CO₂- og PM2.5-måling fra rummene. Alderen bygger på seneste HA-rapport, og gentagen videresendelse nulstiller den ikke.
- Markér den nye aldersprotokol, så Pi 1.13.0-beta.1 kan udelade enkelte for gamle målinger og vise deres kvalitet. Ugyldige eller fremtidige tidsstempler bliver ukendte.
- Bevar kompatibilitet med ældre Pi-versioner. Entity-ID'er, rumopsætning og controllerindstillinger bevares.
- Genstart Home Assistant efter installationen. Den eksisterende indirekte HAC1-eftervarmestyring bevares; direkte 0–10 V-styring er ikke en del af opdateringen.

Se [samlede release notes, versionskrav og validering](docs/releases/0.9.0-beta.13.md).
