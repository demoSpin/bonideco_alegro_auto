# Hetzner serverio paruošimas — instrukcija klientui

> Trumpai: užregistruoti **Hetzner Cloud** paskyrą, sukurti vieną serverį,
> įdėti gautą SSH raktą, atsiųsti IP adresą kūrėjui. Užtruks ~15 min.

---

## Kas yra Hetzner ir kodėl?

Hetzner Cloud — Vokietijos debesies paslaugų teikėjas, vienas pigiausių ES.
Mums reikia mažo virtualaus serverio (~4 €/mėn), kuris veiks 24/7 ir vykdys
Allegro prekių importo bot'ą. Sąskaitas ir mokėjimus tvarkote tiesiogiai su
Hetzner — kūrėjas niekur tarp jūsų ir mokėjimo netrukdys.

---

## Ko reikia iš anksto

Prieš pradedant, gausite iš kūrėjo **vieną tekstinį SSH raktą** (vienos
eilutės tekstas, prasidedantis nuo `ssh-ed25519 AAAA...` arba
`ssh-rsa AAAA...`). Šis raktas leis kūrėjui prisijungti prie serverio.
**Slaptažodžio jokio nereikia siųsti.** Saugu — be jo niekas neprisijungs.

---

## 1 žingsnis — Hetzner Cloud paskyros sukūrimas

1. Atidarykite https://accounts.hetzner.com/signUp
2. **SVARBU**: pasirinkite produktą **„Cloud"** (ne „Robot" / ne „Storage Box")
3. Užpildykite: el. paštas, slaptažodis, vardas, adresas, įmonės pavadinimas
   ir VAT numeris (jei norite gauti įmoninę sąskaitą su PVM)
4. Patvirtinkite el. paštą (laiškas ateina per ~1 minutę)
5. Pirmą kartą sistema gali paprašyti **patvirtinti tapatybę** —
   nufotografuoti pasą ar asmens dokumentą per webcam'ą. Trunka ~3 min,
   patvirtinama paprastai per kelias minutes.
6. Pridėkite mokėjimo kortelę (Hetzner kas mėnesį automatiškai nuskaičiuos
   ~4 €)

---

## 2 žingsnis — Naujo project'o sukūrimas

Po prisijungimo į https://console.hetzner.cloud:

1. Viršuje paspauskite **+ New Project**
2. Pavadinkite: `Bonideco bot`
3. Paspauskite **Create Project**

---

## 3 žingsnis — Pridėti kūrėjo SSH raktą

Project'o viduje:

1. Kairėje meniu raskite **Security** → **SSH Keys**
2. Paspauskite **Add SSH Key** (mygtukas dešinėje)
3. **Public key** lauke įklijuokite raktą, kurį atsiuntė kūrėjas (vienos
   eilutės tekstas, pradedantis `ssh-ed25519` arba `ssh-rsa`)
4. **Name** lauke įveskite: `developer`
5. Paspauskite **Add SSH key**

Raktas dabar bus pasiekiamas kuriant serverius.

---

## 4 žingsnis — Sukurti serverį

1. Kairėje meniu paspauskite **Servers** → **Add Server**
2. Užpildykite:

   | Laukas | Reikšmė |
   |---|---|
   | **Location** | `Helsinki` (geriausia mūsų atveju, ES regionas) |
   | **Image** | `Ubuntu` → `Ubuntu 24.04` |
   | **Type** | Skirtuke **Shared vCPU** → **CX22** (ne Dedicated, ne kiti) |
   | **Networking** | Palikti default (IPv4 + IPv6 įjungti) |
   | **SSH keys** | **Pažymėti** raktą `developer` (kurį pridėjote 3 žingsnyje) |
   | **Volumes / Firewalls / Backups** | Praleisti, default |
   | **Cloud config** | Praleisti |
   | **Name** | `allegro-bot` |
   | **Labels** | Praleisti |

3. Apačioje matysite kainą — `4,51 € / month` ar pan. su PVM
4. Paspauskite **Create & Buy now**
5. Po ~30 sekundžių pamatysite serverio IP adresą — atrodys kaip
   `116.203.123.45` (ar panašiai)

---

## 5 žingsnis — Atsiųsti kūrėjui

Atsiųskite kūrėjui per pokalbį / el. paštą:

> **Hetzner serveris paruoštas.**
> IP: `<jūsų serverio IP>`
> Vartotojas: `root`
> SSH raktas: jūsų atsiųstasis (jau pridėtas)

Tai viskas, ko reikia. Kūrėjas turi viską, ko reikia tolimesniems žingsniams.

---

## Sąskaitos ir mokėjimas

- Hetzner sąskaitos siunčiamos automatiškai į el. paštą **kiekvieno mėnesio
  pradžioje** už praėjusį mėnesį
- PDF su PVM, įmonės rekvizitais — tinka Lietuvos buhalterijai
- Apmokestinama tik faktiškas naudojimas (jei serveris dirbo 30 dienų — pilna
  mėnesio kaina)

## Jei norėsite atjungti

Pakanka per Hetzner console paspausti **Servers** → savo serverį → **Delete**.
Mokėjimas automatiškai sustos po dabartinio mėnesio. Per Telegram'ą bot'as
nustos veikti iškart po serverio sustabdymo.

## Klausimai

Visus techninius klausimus — kūrėjui. Hetzner support'as anglų kalba 24/7.
