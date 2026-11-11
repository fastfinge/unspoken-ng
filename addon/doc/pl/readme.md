# unspoken-ng

Unspoken dla współczesnych wersji NVDA, wykorzystujący OpenAL Soft.

Wersje unspoken-ng z serii 1 korzystały ze Steam Audio. Wymagało to jednak kompilowania kodu źródłowego C++ podczas budowania dodatku i znacznie zwiększało jego rozmiar. Seria 2 korzysta z OpenAL Soft, dzięki czemu dodatek jest mniejszy i nie wymaga już biblioteki libverb.

## Dlaczego?

Poprzednie wersje Unspokena miały niestety wiele poważnych problemów, ponieważ korzystały z biblioteki audio, która nie była już rozwijana ani utrzymywana:

* Nie można było zmienić urządzenia wyjściowego, przez które odtwarzano dźwięki.
* Po kilku godzinach pracy urządzenie audio zaczynało wydawać pisk.
* Przejście NVDA na architekturę 64-bitową lub wersję Pythona nowszą niż 3.11 uniemożliwia korzystanie z tej biblioteki — a zmiany te już nastąpiły.

## Wymagania

* **NVDA 2026.1 lub nowsze, w wersji 64-bitowej.** Dodatek zawiera bibliotekę OpenAL Soft w wersji x64 i jest oznaczony jako niezgodny z wersjami NVDA starszymi niż 2026.1. Starsze NVDA nie pozwoli go włączyć.
* **Słuchawki**, jeśli chcesz korzystać z bardziej zaawansowanych funkcji dodatku. Jego istotą jest pozycjonowanie dźwięków w lewo i w prawo oraz w górę i w dół, czego głośniki laptopa nie są w stanie odtworzyć.
* Jeśli nadal masz zainstalowany oryginalny dodatek **Unspoken 1.x**, usuń go. I tak jest niezgodny z tą wersją NVDA, a oba dodatki ingerują w sposób przetwarzania mowy przez NVDA. Pamiętaj, że podczas aktualizacji stare ustawienia są przenoszone do ustawień opisanych poniżej, a następnie dawne klucze konfiguracyjne są usuwane. Z tych samych kluczy korzystał również oryginalny Unspoken.

## Rozwiązanie

Ta wersja Unspokena korzysta z OpenAL Soft, ładowanego bezpośrednio za pomocą ctypes z dołączonego pliku soft_oal.dll. OpenAL Soft to dobrze udokumentowana biblioteka używana w wielu aplikacjach. Jest więc sprawdzona w praktyce, na bieżąco utrzymywana, a jej błędy są usuwane.

## Podziękowania

W tym projekcie zajmuję się właściwie tylko publikowaniem wydań, dokumentacją i kontaktem z użytkownikami. Unspoken-ng nie mógłby powstać bez pomocy następujących osób:

* Bryan Smart — stworzył dwie pierwotne wersje dodatku Unspoken.
* Masonasons — dostosował dodatek Unspoken do zmian w API w latach 2023 i 2024.
* Ambro86 — utrzymuje aktualne powiązania biblioteki synthizer z Pythonem, a także wniósł część kodu do Unspokena.
* Tyler Spivey — poświęcił czas na rozpracowanie Steam Audio i stworzył powiązania z Pythonem, które zapewniają potrzebne nam funkcje.
* AKJ — przeniósł całość na OpenAL Soft.
* DJ Graco — polskie tłumaczenie i polski podręcznik użytkownika.
* Ja — bardzo potrzebowałem tych funkcji, robiłem, co mogłem, aby projekt działał dalej, i prosiłem innych o pomoc przy wszystkich trudniejszych zadaniach.

## Korzystanie z dodatku

Po zainstalowaniu dodatek dodaje do okna ustawień NVDA kategorię **Unspoken-ng**. Znajduje się w niej sześć ustawień:

* **Motyw dźwiękowy** — zestaw odtwarzanych dźwięków. Do dodatku dołączony jest jeden motyw, *Default* (domyślny). Każdy zainstalowany motyw (patrz niżej) pojawia się na tej liście. Wybrany motyw jest wczytywany już podczas przeglądania listy, więc kolejny dźwięk kontrolki — na przykład po przejściu klawiszem Tab do następnego elementu panelu — pochodzi już z nowego motywu. Anulowanie okna przywraca poprzedni motyw.
* **Oznajmianie ról kontrolek** — sposób oznajmiania ról kontrolek:
    * *Dźwięki* (ustawienie domyślne) — dźwięk zamiast wypowiadanej nazwy roli.
    * *Dźwięki i mowa* — dźwięk **oraz** wypowiadana nazwa roli; przydatne podczas poznawania motywu.
    * *Tylko mowa* — bez dźwięków; NVDA wypowiada nazwy ról tak, jak bez dodatku.
* **Pogłos** — przestrzeń akustyczna, w której umieszczane są dźwięki: *Brak*, *Małe pomieszczenie* (ustawienie domyślne), *Średnie pomieszczenie* lub *Sala*. Większa przestrzeń ułatwia rozpoznawanie położenia i nieco wydłuża wybrzmiewanie dźwięku.
* **Głośność dźwięków** — głośność dźwięków ról kontrolek, od 0 do 100; domyślnie 100. Ustawienie to działa dodatkowo względem ustawień samego NVDA. Dźwięki ról już uwzględniają ustawioną w NVDA głośność dźwięków albo głośność mowy, jeśli w ustawieniach dźwięku NVDA włączono dostosowywanie głośności dźwięków do głośności mowy. Ten suwak pozwala dodatkowo zmniejszyć ich poziom. Zmiany są stosowane podczas przesuwania suwaka, więc kolejny dźwięk kontrolki jest już odtwarzany z nową głośnością.
* **Mowa przestrzenna** — określa, czy mowa syntezatora ma dochodzić z tego samego miejsca co dźwięk roli, tak aby dźwięk i słowa opisujące kontrolkę miały to samo położenie:
    * *Brak* (ustawienie domyślne) — mowa jest odtwarzana dokładnie tak, jak bez dodatku.
    * *Panoramowanie* — mowa jest przesuwana w panoramie stereo w lewo lub w prawo, zgodnie z położeniem elementu, do którego przechodzisz: kontrolki z fokusem, obiektu osiągniętego za pomocą nawigacji po obiektach lub pola osiągniętego za pomocą szybkiej nawigacji. Nadal jest odtwarzana przez mechanizm wyjścia audio samego NVDA.
    * *Panoramowanie i HRTF* — mowa jest pozycjonowana w przestrzeni 3D, zarówno w pionie, jak i w poziomie, przez ten sam silnik, który odtwarza dźwięki ról kontrolek. Mowa zawsze pozostaje bez pogłosu: ustawienie **Pogłos** dotyczy wyłącznie dźwięków ról. Korzystaj ze słuchawek. Jeśli dodatek działa bez obsługi dźwięku, zamiast tego trybu używane jest *Panoramowanie*.

    Położenie mowy zmienia się tylko między wypowiedziami, nigdy w środku zdania. Podczas działania funkcji „Czytaj wszystko” pozostaje zgodne z położeniem ostatnio osiągniętego elementu. Zmiany ustawienia są stosowane od następnej wypowiedzi NVDA. Funkcja działa z syntezatorami uruchamianymi wewnątrz procesu NVDA, czyli między innymi z eSpeak, Windows OneCore, SAPI 5 i większością syntezatorów dostarczanych jako dodatki. Głosy SAPI 4 i 32-bitowe głosy SAPI 5, które NVDA uruchamia w osobnym procesie, nie są pozycjonowane przestrzennie.

* **Wycisz dźwięki ról kontrolek podczas „Czytaj wszystko”** — domyślnie wyłączone. Włącz tę opcję, jeśli dźwięki rozpraszają Cię podczas ciągłego czytania.

Jeśli dodatek w ogóle nie może odtwarzać dźwięków — z powodu braku urządzenia audio lub uszkodzonej instalacji — informuje o tym jednorazowo krótko po uruchomieniu NVDA, a następnie do końca sesji korzysta wyłącznie z mowy. Ustawienia pozostają zachowane, więc po usunięciu problemu dźwięki wrócą bez potrzeby ponownej konfiguracji.

## Tłumaczenia

Dodatek jest dostępny w języku angielskim i polskim. NVDA sam wybiera język: jeśli NVDA działa po polsku, okno ustawień dodatku, wypowiadane przez niego komunikaty, nazwa i opis dodatku w sklepie z dodatkami NVDA oraz w menedżerze dodatków, a także podręcznik otwierany przyciskiem Pomoc są po polsku.

*Polski — DJ Graco: teksty interfejsu i podręcznik użytkownika.*

Jeśli Twojego języka jeszcze nie ma, zobacz sekcję **Tłumaczenie** poniżej. Tłumaczenie to zwykły katalog gettext, a opcjonalnie także przetłumaczona kopia tego podręcznika; wystarczy jeden pull request, aby trafiło do wydania. Podczas budowania automatycznie dołączany jest każdy katalog z `addon/locale` i każdy podręcznik z `addon/doc`, bez dodatkowej konfiguracji.

## Motywy dźwiękowe

Motyw dźwiękowy to po prostu folder zawierający pliki WAV o odpowiednich nazwach. Aby go zainstalować, umieść jego folder w następującym katalogu:

`%APPDATA%\nvda\unspoken-ng\sound-themes\`

Jest to ścieżka dla zainstalowanej kopii NVDA. W kopii przenośnej jest to katalog `unspoken-ng\sound-themes\` wewnątrz folderu kopii przenośnej — zawsze znajduje się obok pliku `nvda.ini`. Dodatek tworzy ten katalog podczas uruchamiania. Uruchom ponownie NVDA lub ponownie otwórz panel ustawień, a motyw pojawi się na liście **Motyw dźwiękowy**. Nazwa folderu jest identyfikatorem motywu.

Aby utworzyć motyw, przygotuj pliki odpowiadające tym elementom, dla których chcesz zdefiniować własne dźwięki. Użyj następujących nazw:

`button`, `checkbox`, `clock`, `combobox`, `editabletext`, `icon`, `link`, `listitem`, `menuitem`, `radiobutton`, `slider`, `splitbutton`, `tab`, `treeviewitem`

Pełne nazwy plików to zatem `button.wav`, `link.wav` i tak dalej. Dodatek sam przypisuje role kontrolek NVDA do odpowiednich dźwięków — nie musisz znać szczegółów tego przypisania.

**Motyw nie musi zawierać wszystkich dźwięków.** Każdy brakujący dźwięk jest pobierany z dołączonego motywu domyślnego, więc motyw zastępujący tylko plik `button.wav` jest w pełni poprawny.

Pliki muszą być nieskompresowanymi plikami WAV PCM, mono lub stereo, o rozdzielczości 16 albo 24 bitów. Obsługiwana jest dowolna częstotliwość próbkowania — dźwięk stereo jest miksowany do mono, a częstotliwość próbkowania jest uwzględniana podczas odtwarzania. Nieprawidłowy plik jest pomijany, a informacja o tym trafia do logu; nie przerywa to działania całego motywu.

Opcjonalnie obok plików WAV możesz umieścić plik `theme.ini`:

    [theme]
    name = My Theme
    author = Your Name
    description = What it sounds like
    gain = 0

Tylko pole `name` wpływa na nazwę wyświetlaną na liście w ustawieniach. Parametr `gain` określa dodatkową korektę poziomu w decybelach, ograniczoną do zakresu ±12 dB. Jest stosowany po normalizacji głośności, którą dodatek przeprowadza dla całego motywu. Używaj go tylko wtedy, gdy Twój motyw jest wyraźnie głośniejszy lub cichszy od domyślnego.

**Wyrównaj poziomy poszczególnych dźwięków.** Dodatek normalizuje motyw jako całość, celowo zachowując ustalone przez Ciebie proporcje głośności między dźwiękami. Oznacza to jednak, że jeden wyjątkowo głośny plik ogranicza poziom wszystkich pozostałych. Jeśli motyw nie może osiągnąć docelowej głośności bez przesterowania tego pliku, dodatek automatycznie ścisza cały motyw, aby uniknąć zniekształceń. W rezultacie wszystkie pozostałe dźwięki również stają się cichsze. Motyw, w którym poziomy poszczególnych dźwięków różnią się o najwyżej kilka dB, osiągnie zamierzoną głośność; motyw z jednym nadmiernie głośnym dźwiękiem — nie.

## Budowanie dodatku

Dodatek NVDA buduje się za pomocą SCons. Zawiera on oficjalną bibliotekę OpenAL Soft dla Windows w wersji x64 (soft_oal.dll); nie trzeba kompilować żadnego kodu natywnego.

Sam dodatek nie wymaga dodatkowych zależności — działa przy użyciu Pythona dostarczanego z NVDA. Zestaw testów uruchamianych poza NVDA wymaga tylko pytest:

`python -m pip install pytest`

`python -m pytest tests/`

Plik `pyproject.toml` dzieli narzędzia na grupy, dzięki czemu [uv](https://docs.astral.sh/uv/) może pobierać je w razie potrzeby: `uv run --group test pytest tests/`, `uv run --group build scons`.

## Tłumaczenie

Wszystkie teksty widoczne dla użytkownika są objęte obsługą gettext, więc tłumaczenia mają postać zwykłych katalogów gettext i można je zgłaszać przez pull requesty:

1. Pobierz szablon tłumaczenia. Do każdego wydania dołączany jest plik `Unspoken-ng.pot`. Możesz też wygenerować go samodzielnie poleceniem `scons pot`. Wymaga to [GNU gettext](https://mlocati.github.io/articles/gettext-iconv-windows.html) dostępnego w zmiennej PATH; gettext jest również potrzebny do budowania dodatku, gdy zawiera on już tłumaczenia.
2. Utwórz na podstawie szablonu plik `addon/locale/<language>/LC_MESSAGES/nvda.po`, gdzie `<language>` to kod języka używany przez NVDA (`de`, `fr`, `pt_BR` itd.). Możesz skorzystać z edytora plików PO, takiego jak [Poedit](https://poedit.net/), lub z polecenia `msginit`.
3. Przetłumacz teksty. Szablon zawiera również krótką nazwę i opis dodatku z pliku `buildVars.py`. Przetłumaczenie tych dwóch wpisów zapewnia wyświetlanie nazwy i opisu dodatku w wybranym języku w sklepie z dodatkami NVDA oraz w menedżerze dodatków.
4. Opcjonalnie przetłumacz podręcznik użytkownika: umieść przetłumaczoną kopię tego pliku readme w `addon/doc/<language>/readme.md`.
5. Utwórz pull request z plikiem `.po` oraz przetłumaczonym plikiem readme, jeśli został przygotowany. Nie dodawaj do commita skompilowanych plików `.mo` ani wygenerowanych plików `manifest.ini` — powstają one podczas budowania dodatku i są ignorowane przez Git.

Aby zaktualizować istniejące tłumaczenie po zmianie tekstów źródłowych, scal z nim nowy szablon za pomocą polecenia `msgmerge --update nvda.po Unspoken-ng.pot` lub opcji aktualizacji z pliku POT w Poedicie, a następnie uzupełnij nowe wpisy.

Polski jest kompletnym przykładem obu tych plików: `addon/locale/pl/LC_MESSAGES/nvda.po` oraz `addon/doc/pl/readme.md`.

## Publikowanie wydań

Opublikuj wydanie na GitHubie z tagiem zawierającym sam numer wersji, bez prefiksu `v` (np. `2.1`, zgodnie z konwencją `X.Y` stosowaną w sklepie z dodatkami NVDA) — to cały proces. Numer wersji dodatku jest ustalany podczas budowania na podstawie wyniku `git describe --tags`, więc to tag określa wersję. System ciągłej integracji (CI) ponownie buduje dodatek ze wskazanego tagu — po uprzednim uruchomieniu testów i weryfikacji artefaktów — a następnie dołącza plik `.nvda-addon` do wydania. Nie trzeba ręcznie zmieniać numeru wersji, budować dodatku ani przesyłać plików. Lokalne kompilacje tworzone między tagami otrzymują numer wersji w rodzaju `2.0-51-g3b71875`.

## Znane problemy

Obecnie brak. Jeśli jakiś znajdziesz, z przyjemnością i wdzięcznością przyjmę zgłoszenie lub pull request.

Naprawiono w wersji 2.7: dodatek nie zawierał żadnych tłumaczeń — wszystkie teksty widoczne dla użytkownika były objęte obsługą gettext, a podczas budowania powstawał szablon `.pot`, ale żaden katalog tłumaczeń nie trafiał do paczki dodatku, więc wszędzie wyświetlał się on po angielsku. Teraz razem z dodatkiem trafiają polski katalog tłumaczeń i polski podręcznik użytkownika, a każdy katalog z `addon/locale` i każdy podręcznik z `addon/doc` jest dołączany do paczki, więc dodanie języka nie wymaga zmian w procesie budowania.

Naprawiono w wersji 2.0: Unspoken-ng nie odtwarzał dźwięku podczas przechodzenia strzałkami po niektórych kontrolkach w internecie. Położenie kontrolki było bowiem niedostępne, dopóki nie otrzymała ona fokusu, a NVDA nie przenosi już fokusu systemowego wraz z kursorem przeglądania. Czytanie w trybie przeglądania jest teraz obsługiwane osobno i nie wymaga przeniesienia fokusu.

## Zobowiązanie do utrzymania projektu

Ja, Samuel Proulx, znany również jako fastfinge, publicznie zobowiązuję się do dalszego utrzymywania wszystkich funkcji dodatku dostępnych obecnie w repozytorium fastfinge/unspoken-ng, aby zapewnić ich zgodność ze zmianami w API NVDA. Jeśli sam nie będę w stanie tego robić, zatrudnię kogoś, kto zrobi to w moim imieniu. Sam polegam na tych funkcjach przy wykonywaniu niektórych kluczowych zadań. Dodatek w obecnej postaci spełnia jednak moje potrzeby. Jeśli chcesz zająć się którymś z wymienionych wyżej znanych problemów, serdecznie zachęcam do przesłania pull requesta. Po jego zaakceptowaniu będę utrzymywać dodane funkcje. Problemy te nie wpływają jednak na moją pracę, dlatego nie będę sam ich rozwiązywać.
