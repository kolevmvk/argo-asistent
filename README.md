# Ljilja Assistant

Ljilja je lokalni lični asistent za Mac mini. Telegram je glavni ulaz i izlaz, Notion je baza i kalendar, a Jira se koristi samo eksplicitno kroz `/jira`.

## Pokretanje

```bash
cp .env.example .env
python3 -m ljilja_assistant
```

U `.env` popuni:

```env
TELEGRAM_BOT_TOKEN=
TELEGRAM_ALLOWED_CHAT_ID=
NOTION_TOKEN=
NOTION_DATABASE_ID=
OLLAMA_URL=http://localhost:11434
OLLAMA_MODEL=gemma3:4b
TIMEZONE=Europe/Belgrade
DRY_RUN=true
DAILY_REPORT_TIME=21:30
JIRA_BASE_URL=
JIRA_EMAIL=
JIRA_API_TOKEN=
JIRA_PROJECT_KEY=
JIRA_ISSUE_TYPE=Task
```

Kada je `DRY_RUN=true`, Ljilja ne upisuje u Notion i ne kreira Jira issue. Samo vraća šta bi uradila.

Za prvu probu ostavi `DRY_RUN=true`. Kada Telegram, Ollama i parser rade kako treba, popuni `NOTION_TOKEN` i `NOTION_DATABASE_ID`, promeni `DRY_RUN=false` i restartuj proces.

## Telegram

1. Otvori BotFather u Telegramu.
2. Pošalji `/newbot`.
3. Izaberi ime i username.
4. Token upiši u `TELEGRAM_BOT_TOKEN`.

Kako dobiti `chat_id`:

1. Pokreni bota sa tokenom.
2. Pošalji poruku botu.
3. Otvori `https://api.telegram.org/bot<TOKEN>/getUpdates`.
4. Vrednost `message.chat.id` upiši u `TELEGRAM_ALLOWED_CHAT_ID`.

Bot koristi long polling, bez webhooka.

Obična pitanja idu na lokalnu Ollamu (`OLLAMA_URL`, `OLLAMA_MODEL`). Na primer `ko si ti?` ili `šta možeš?` neće se tretirati kao Notion zadatak. Kada Ljilja mora da pozove lokalni model, prvo šalje `Razmišljam...`, pa zatim odgovor. Ako Ollama pukne ili istekne, Ljilja neće oboriti proces. `DRY_RUN` utiče na upis u Notion i kreiranje Jira issue-a, ali ne gasi AI razgovor.

Podrazumevani Ollama profil je podešen za `gemma3:4b`: kraći odgovori, kratki timeout-i, manji output budžet i determinističniji JSON intent parsing. Ako koristiš veći/sporiji model, možda ćeš želeti da povećaš timeout-e u `ljilja_assistant/ollama_client.py`.

Kada Ljilja upiše stavku u Notion ili pošalje podsetnik, poruka dobija dugmad:

- `Urađeno` postavlja status `Done`
- `Kasnije` pomera sledeći podsetnik za 10 minuta
- `Sutra` postavlja status `Moved` i pomera `Date` na sutra u 09:00
- `Preskoči danas` pomera proveru za sutra u 09:00

## Komande

- `/start`
- `/help`
- `/status`
- `/mac`
- `/danas`
- `/sutra`
- `/izvestaj`
- `/jira naslov tiketa`

Primeri unosa:

- `Ljiljo jesi tu`
- `ko si ti`
- `zadatak: proveri ovo`
- `sutra u 09 proveri Gmail filtere`
- `15.07.2026 u 20h backup Mac-a`
- `u petak 18:30 sastanak ellco.pro`
- `beleška: danas sam rešio Telegram unos`
- `plan za sutra: 1h JuristSoft, 30min ellco.pro`
- `Ljiljo podseti me za 5 minuta da idem`
- `Ljiljo proveravaj svaki dan u 21 da li sam uneo izveštaj`
- `Ljiljo podsećaj me svakog ponedeljka u 8 da proverim Jira`
- `Ljiljo podsećaj me radnim danima u 8 da proverim Jira`
- `Ljiljo podsećaj me svakog 15. u mesecu u 10 da platim hosting`
- `Ljiljo podsećaj me svakog 15.07 u 10 da proverim ugovor`
- `Ljiljo treba da završim ponudu do petka`
- `Ljiljo dežuran sam 15.07.2026 od 20h u JuristSoft`

Ljilja je AI-first: obične poruke idu na lokalni model. Podsetnici, planovi, beleške, dežurstva i jasne stavke sa datumom/vremenom idu u Notion.

Notion je Ljiljina dugoročna memorija. Poruke tipa `zapamti da...`, `zabeleži...`, `upamti...` i slične upisuju se kao `Note`, a pri običnom razgovoru Ljilja povlači skoriji razgovor i relevantne Notion stavke kao kontekst za Gemmu. Tako može da odlučuje iz konteksta umesto da svaku poruku tretira kao izolovan unos.

Pre live upisa Ljilja pokušava da poveže novi unos sa postojećom Notion memorijom. Ako je veza jasna, dopunjava `Project` i `Result`. Ako ima više mogućih veza ili fali ključna informacija, pita kratko dopunsko pitanje umesto da napravi loš zapis.

Brzi lokalni parser pokriva česte obrasce bez čekanja modela: `danas`, `sutra`, dan u nedelji, `do petka`, konkretan datum, `svaki dan`, `radnim danima`, `svakog ponedeljka`, `svakog 15. u mesecu` i `svakog 15.07`. Ako poruka nije jasna, tek tada se pali lokalni AI.

Jednokratni podsetnici i taskovi koriste `Date`. Ponavljajuće provere koriste `NextCheck`, `Repeat` i `CheckMode`, pa Ljilja može sama da pita da li je nešto urađeno. Dežurstva se upisuju kao `Duty` sa `Date` i `Location`, pa ih Notion Calendar vidi preko `Date` polja.

`/danas` i `/sutra` čitaju Notion stavke za taj dan. `/izvestaj` sabira današnje statuse i ispisuje listu stavki. Automatski dnevni izveštaj se šalje jednom dnevno u `DAILY_REPORT_TIME`.

## Notion

Napravi jednu Notion database i integration poveži sa tom bazom.

Obavezna polja:

- `Title` kao Title
- `Type` kao Select
- `Project` kao Rich text
- `Location` kao Rich text
- `Date` kao Date
- `NextCheck` kao Date
- `LastAsked` kao Date
- `Repeat` kao Select
- `CheckMode` kao Select
- `Status` kao Select
- `Priority` kao Select
- `Source` kao Select
- `Result` kao Rich text
- `Score` kao Number

`Type` opcije: `Task`, `Note`, `Plan`, `Reminder`, `Event`, `Duty`.

`Repeat` opcije: `None`, `Daily`, `Weekdays`, `Weekly`, `Monthly`, `Yearly`.

`CheckMode` opcije: `Reminder`, `AskUntilDone`.

Status opcije:

- `Planned`
- `In Progress`
- `Done`
- `Partial`
- `Skipped`
- `Moved`
- `Cancelled`

Notion Calendar će prikazivati stavke preko `Date` polja.

Setup:

1. Napravi internal integration na `https://www.notion.so/my-integrations`.
2. Kopiraj secret u `NOTION_TOKEN`.
3. U Notion bazi izaberi `Connections` i dodaj integration.
4. Iz URL-a baze uzmi database ID i upiši u `NOTION_DATABASE_ID`.

Ako te mrzi da ručno praviš bazu, napravi praznu Notion stranicu, dodaj integration u `Connections`, pa pokreni:

```bash
python3 -m ljilja_assistant.notion_setup PARENT_PAGE_ID Ljilja
```

Komanda će napraviti database sa svim potrebnim poljima i ispisati `NOTION_DATABASE_ID`.

## Jira

Jira je opciona. Ljilja je koristi samo za `/jira naslov tiketa`, nikada automatski.

Popuni:

- `JIRA_BASE_URL`, npr. `https://firma.atlassian.net`
- `JIRA_EMAIL`
- `JIRA_API_TOKEN`
- `JIRA_PROJECT_KEY`
- `JIRA_ISSUE_TYPE`, npr. `Task`

Ako bilo koja JIRA varijabla nedostaje, `/jira` će javiti da Jira nije podešena.

## Mac Status

`/mac` je read-only i prikazuje:

- disk usage
- uptime
- load
- `memory_pressure` ako postoji na sistemu

Nema brisanja, slanja emailova ni destruktivnih akcija.

## launchd

1. Kopiraj template:

```bash
cp launchd/com.local.ljilja-assistant.plist.template ~/Library/LaunchAgents/com.local.ljilja-assistant.plist
```

2. U plist fajlu zameni `/ABSOLUTE/PATH/TO/python3` rezultatom komande `which python3`, a `/ABSOLUTE/PATH/TO/argo-assistant` stvarnom putanjom projekta.

3. Učitaj servis:

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.local.ljilja-assistant.plist
```

4. Pokreni odmah:

```bash
launchctl kickstart -k gui/$(id -u)/com.local.ljilja-assistant
```

5. Zaustavi i izbaci servis:

```bash
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.local.ljilja-assistant.plist
```

Logovi su u `/tmp/ljilja-assistant.out.log` i `/tmp/ljilja-assistant.err.log`.

## Testovi

```bash
python3 -m compileall .
python3 -m unittest discover -s tests
```
