# Changelog

## 0.9.0-beta.17
Udkast til udgivelsesbeskrivelse. Versionsnummeret sættes ved udgivelsen. Ændret af Claude AI.

## Rettet

- **Forbindelsen til RS485-strømmen kan ikke længere gå i stå.** Hvis forbindelsen blev nulstillet, døde baggrundsopgaven stille, og den kom først i gang igen ved genindlæsning. Nu forbinder den igen af sig selv med stigende ventetid, og enhederne står som utilgængelige imens. Forbindelsen tjekkes nu hvert minut, så reparationsbeskeden "Ingen forbindelse" også kommer, når strømmen er helt stille.
- **Uventet fejl i forbindelsen til Pi-controlleren** fik før enhederne til at stå frosne som tilgængelige. Nu logges en advarsel, enhederne bliver utilgængelige, og forbindelsen prøves igen. Står controlleren uden svar i længere tid end 30 sekunder, regnes den for væk.
- **Reparation og ny nøgle til controlleren.** Er controlleren væk i mere end 15 minutter, kommer en klar reparationsbesked. Afviser Pi'en nøglen (401/403), starter Home Assistant selv "Godkend igen", hvor du indtaster nøglen på ny. Der kommer en særskilt reparationsbesked, hvis nøglen stadig ikke virker efter 15 minutter.
- **Genstart af Home Assistant nulstiller ikke længere målingernes alder.** Integrationen husker, hvornår hver rumføler sidst rapporterede, og sender den rigtige alder til Pi'en, hvis værdien er uændret efter en genstart. Så virker Pi'ens vagt på 3 timer også lige efter en genstart. Nye målinger tæller som friske med det samme.
- **Pi'en får rum og katalog med det samme, når den kommer tilbage** efter en genstart, i stedet for at vente op til et minut.
- **Integrationens unikke id følger adressen.** Skifter du IP eller port under indstillinger, opdateres integrationens interne id, så den gamle adresse ikke hænger fast. Findes adressen allerede i en anden indgang, får du besked. Enheds- og entitets-id'er er uændrede.
- **Forældreløse udsugningsentiteter ryddes op.** "Udsugningskanal afkøling", "Udsugningskanal andel tabt" og "Udsugningsrum temperatur" fjernes fra registret, hvis Pi'en ikke har haft en værdi til dem i 14 dage, mens controlleren er i gang. Får de en værdi igen, oprettes de på ny. Andre valgfrie entiteter røres ikke.

## Nyt

- **Dugpunkt sendes med.** Hvert rum med både temperatur og luftfugtighed sender nu dugpunktet med, og vejrdata får et udregnet dugpunkt, hvis vejrentiteten ikke har et. Det bruges til at regne udeluftens fugt om til enhedens T1. Selve omregningen og valget af kilde sker på Pi'en.
- **Slået fra som standard:** "Se en sensor som levende, når dens enhed sender andre værdier" (under controllerens indstillinger). Når den er slået til, sender integrationen også, hvor længe siden hele enheden bag hver sensor sidst rapporterede noget. Det gælder fx en PM2.5-føler, der kun melder ved ændring, mens CO2 på samme enhed melder hvert sekund. Pi'en skal kunne læse feltet, før det har virkning.

## Skal du gøre noget

- Intet er påkrævet. Pi-siden af tidsvagten og dugpunktet kræver den tilsvarende Pi-version.
- Udeluftens fugt: sæt "kilde til udeluftens fugt" til vejrentiteten på Pi'en (WebUI), så bruges dugpunktet.

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
