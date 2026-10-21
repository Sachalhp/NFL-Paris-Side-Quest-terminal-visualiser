# 🏈 Coach's Film Room

Raw NFL tracking data is millions of (x, y) coordinates that no coach, journalist, or fan can actually read. **Coach's Film Room turns it into something anyone understands instantly: it replays any play as 22 moving players on a terminal football field, with a live "QB Pressure" meter that measures, 10 times a second, how close the nearest pass-rusher is to the quarterback.** You watch the pocket form and collapse, see who won the protection rep, and the tool writes the verdict in plain English. It's for a coach reviewing film, a journalist finding the story in a play, or a fan who just wants to see what happened, no spreadsheets, no jargon. Built in one sitting with Python, pandas, and Textual.

## ▶️ Run it (ships with 2 real games, works immediately)

```bash
git clone https://github.com/YOUR_USERNAME/coachs-film-room.git
cd coachs-film-room
python3 -m venv venv && ./venv/bin/pip install -r requirements.txt
./venv/bin/python3 main.py
```

**Try this:** pick a game → set the filter to **Sacks** → open a play → press **⚡ Jump to pressure** and watch the meter spike red. Press `H` anytime for all controls. Use a wide, maximized terminal.
