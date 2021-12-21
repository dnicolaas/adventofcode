from gevent.pywsgi import WSGIServer
from flask import Flask, abort, render_template
from urllib.request import Request, urlopen
from urllib.error import HTTPError
import json
from collections import defaultdict
from operator import itemgetter
import datetime
import os
import time
from pprint import pprint

app = Flask(__name__)

# Maximum year you want to support
THISYEAR = 2021

# (In your browser, log in to Advent of Code, open devtools, and look at network traffic.
# Click 'Cookies' on the right, and select and copy the session cookie.)
COOKIE = "<Your cookie here>"

# -*-*-*- Nothing below this line needs to be changed -*-*-*-
YEARSSUPPORTED = range(2015, THISYEAR + 1)


class CachedFileArray():
    """Manage an array of score files.

    Use the only public method get_data to get your score data for a year. It will make sure it is served
    from memory, disk or https, refreshing the cache when it's old, caching it when it is retrieved.
    """
    @staticmethod
    def _json_url(year):
        return f"https://adventofcode.com/{year}/leaderboard/private/view/437700.json"

    @staticmethod
    def _cache_file(year):
        return f"{year}leaderboard.json"

    def __init__(self):
        self._scores = {}

    def _get_data_https(self, year):
        print(f"Refreshing cache for {year}...")
        req = Request(self._json_url(year))
        req.add_header('Cookie', f'session={COOKIE}')
        content = urlopen(req).read()
        with open(self._cache_file(year), "wb") as f:
            f.write(content)
        self._scores = json.loads(content)

    def _get_data_file(self, year):
        print(f"Reading cache for {year}...")
        with open(self._cache_file(year), "r", encoding='utf-8') as f:
            self._scores = json.load(f)

    def _cache_valid(self, year):
        try:
            ftime = os.path.getmtime(self._cache_file(year))
        except FileNotFoundError:
            ftime = 0.0
        return (ftime >= time.time() - (15 * 60))

    def has_new_data(self, year):
        return year not in self._scores or not self._cache_valid(year)

    def get_data(self, year):
        if self._cache_valid(year):
            if year not in self._scores:
                self._get_data_file(year)
        else:
            try:
                self._get_data_https(year)
            except HTTPError:
                return None
        return self._scores


class Scoreboard():
    """Encapsulate the data.

    The ScoreGetter will manage the retrieving and caching of data. We crunch it here and make it available.
    When you want to get a score, call prepare_scores and the get the field.
    """
    @staticmethod
    def mkname(rec):
        name = rec["name"]
        if name is None:
            name = f"(anonymous user #{rec['id']})"
        return name

    def __init__(self, score_getter):
        self._score_getter = score_getter
        self._scores = {}
        self.num_users = {}
        self.star1 = {}
        self.star2 = {}
        self.gold_star_delta = {}
        self.gold_star_delta_sum = {}
        self.scoreboard_name = {}
        self.completion_stats = {}
        self.leaderboard = {}
        self.last_day = 0

    def prepare_scores(self, year):
        if self._score_getter.has_new_data(year):
            self._scores[year] = self._score_getter.get_data(year)
        self.num_users[year] = len(self._scores[year]["members"])
        # scoreboard name
        owner_id = self._scores[year]["owner_id"]
        for id, rec in self._scores[year]["members"].items():
            if id == owner_id:
                name = self.mkname(rec)
                self.scoreboard_name[year] = f"{year} leaderboard of {name}"

        # get last puzzle unlocked
        now = time.time()
        timediff = datetime.timedelta(hours=-5) # from https://adventofcode.com/2021/about
        tz = datetime.timezone(timediff)
        aocdate = datetime.datetime.fromtimestamp(now, tz)
        self.last_day = 25
        if year == THISYEAR and aocdate.month == 12:
            self.last_day = min(25, aocdate.day)
        # Leaderboard
        leaderboard = {}
        for id, rec in self._scores[year]["members"].items():
            name = self.mkname(rec)
            score = rec["local_score"]
            stars = [0] * self.last_day + [-1] * (25 - self.last_day)
            for (day, data) in rec["completion_day_level"].items():
                if "1" in data:
                    stars[int(day) - 1] += 1
                if "2" in data:
                    stars[int(day) - 1] += 1

            leaderboard[name]  = (score, stars)
        # Sort for the template, the local variable is for here
        self.leaderboard[year] = [(name, score, stars) for name, (score, stars) in leaderboard.items()]
        self.leaderboard[year].sort(key=itemgetter(1), reverse=True)

        # get star times and time to finish gold star
        self.star1[year] = defaultdict(list)
        self.star2[year] = defaultdict(list)
        self.gold_star_delta[year] = defaultdict(list)
        for id, rec in self._scores[year]["members"].items():
            name = self.mkname(rec)
            cdl = rec["completion_day_level"]
            for day, data in cdl.items():
                if "1" in data:
                    self.star1[year][int(day)].append((name, int(data["1"]["get_star_ts"])))
                if "2" in data:
                    self.star2[year][int(day)].append((name, int(data["2"]["get_star_ts"])))
                if '1' in data and '2' in data:
                    timediff = int(data['2']['get_star_ts']) - int(data['1']['get_star_ts'])
                    self.gold_star_delta[year][int(day)].append((name, timediff))
        # add all contenders who didn't make it, but are contending
        for (name, (score, stars)) in leaderboard.items():
            if score == 0:
                continue
            for day, data in self.star1[year].items(): # day, list of tuples
                if name not in [name for name, _ in data]:
                    self.star1[year][day].append((name, int(1e11)))
            for day, data in self.star2[year].items():
                if name not in [name for name, _ in data]:
                    self.star2[year][day].append((name, int(1e11)))
            for day, data in self.gold_star_delta[year].items():
                if name not in [name for name, _ in data]:
                    self.gold_star_delta[year][day].append((name, int(1e11)))


        # Temporary structure to sort by time, then score, and pass the stars
        gold_star_delta_cumulative = defaultdict(dict)
        # loop through days in order
        last_day = None
        for day, gsds in sorted(self.gold_star_delta[year].items(), key=itemgetter(0)):
            for name, timedelta in gsds:
                if day == 1:
                    cumu = timedelta
                    score = leaderboard[name][0]
                    stars = leaderboard[name][1]
                else:
                    (oldcumu, score, stars) = gold_star_delta_cumulative[day - 1][name]
                    if timedelta != int(1e11) or oldcumu <= int(1e11):
                        cumu = oldcumu + timedelta
                    else:
                        cumu = oldcumu
                gold_star_delta_cumulative[day][name] = (cumu, score, stars)
            last_day = day
        flat_gold_star_delta = [(score, cumu, name, stars) for name, (cumu, score, stars) in gold_star_delta_cumulative[last_day].items()]
        flat_gold_star_delta = sorted(flat_gold_star_delta, key=(lambda tup: (-sum(tup[3]), tup[1])))
        self.gold_star_delta_sum[year] = [(name, cumu, stars) for (score, cumu, name, stars) in flat_gold_star_delta]

        # remove unsolved from star1, star2 and gold_star_delta
        for day in self.star1[year]:
            self.star1[year][day] = [(name, ts) for name, ts in self.star1[year][day] if ts < 1e11]
            self.star1[year][day].sort(key=itemgetter(1))
        for day in self.star2[year]:
            self.star2[year][day] = [(name, ts) for name, ts in self.star2[year][day] if ts < 1e11]
            self.star2[year][day].sort(key=itemgetter(1))

        for day in self.gold_star_delta[year]:
            self.gold_star_delta[year][day] = [(name, ts) for name, ts in self.gold_star_delta[year][day] if ts < 1e11]
            self.gold_star_delta[year][day].sort(key=itemgetter(1))
        # get completion stats
        # sorted by day
        self.completion_stats[year] = [(0, 0, self.num_users[year]) for _ in range(25)]
        for day in range(25, 0, -1):
            if day in self.star2[year]:
                numgold = len(self.star2[year][day])
            else:
                numgold = 0
            if day in self.star1[year]:
                numsilver = len(self.star1[year][day]) - numgold
            else:
                numsilver = 0
            numnone = self.num_users[year] - numsilver - numgold
            self.completion_stats[year][25 - day] = (numgold, numsilver, numnone)



SCOREBOARD = Scoreboard(CachedFileArray())

@app.context_processor
def global_vars():
    return dict(
        YEARSSUPPORTED=YEARSSUPPORTED,
        THISYEAR=THISYEAR,
    )


@app.template_filter('formatdatetime')
def format_datetime(value):
    """Format a date time to (Default): yyyy-mm-dd hh:mm:ss"""
    if value is None:
        return ""
    return datetime.datetime.fromtimestamp(value)


@app.template_filter('formatduration')
def format_duration(value):
    """Format a time delta to (Default): days, hh:mm:ss"""
    if value is None:
        return ""
    return datetime.timedelta(seconds=value)


@app.route('/')
def index():
    return scores(THISYEAR)


@app.route('/<int:year>')
def scores(year):
    if year in YEARSSUPPORTED:
        SCOREBOARD.prepare_scores(year)
        scoreboard_name = SCOREBOARD.scoreboard_name[year]
        leaderboard = SCOREBOARD.leaderboard[year]
        numusers = SCOREBOARD.num_users[year]
        return render_template('leaderboard.jinja2',
                               scoreboard_name=scoreboard_name,
                               year=year,
                               numusers=numusers,
                               leaderboard=leaderboard,
                               )
    abort(404)


@app.route('/<int:year>/perday')
def scores_per_day(year):
    if year in YEARSSUPPORTED:
        SCOREBOARD.prepare_scores(year)
        scoreboard_name = SCOREBOARD.scoreboard_name[year]
        star1 = SCOREBOARD.star1[year]
        star2 = SCOREBOARD.star2[year]
        return render_template('scoresperdays.jinja2',
                               scoreboard_name=scoreboard_name,
                               year=year,
                               star1=star1,
                               star2=star2,
                               )
    abort(404)


@app.route('/<int:year>/stats')
def stats(year):
    if year in YEARSSUPPORTED:
        SCOREBOARD.prepare_scores(year)
        scoreboard_name = SCOREBOARD.scoreboard_name[year]
        completion = SCOREBOARD.completion_stats[year]
        return render_template('stats.jinja2',
                               scoreboard_name=scoreboard_name,
                               year=year,
                               completion=completion,
                               )
    abort(404)

@app.route('/<int:year>/goldperday')
def gold_per_day(year):
    if year in YEARSSUPPORTED:
        SCOREBOARD.prepare_scores(year)
        scoreboard_name = SCOREBOARD.scoreboard_name[year]
        gold_star_delta = SCOREBOARD.gold_star_delta[year]

        return render_template('goldperday.jinja2',
                               scoreboard_name=scoreboard_name,
                               year=year,
                               gold_star_delta=gold_star_delta,
                               )
    abort(404)

@app.route('/<int:year>/gold')
def gold(year):
    if year in YEARSSUPPORTED:
        SCOREBOARD.prepare_scores(year)
        scoreboard_name = SCOREBOARD.scoreboard_name[year]
        gold_star_delta_sum = SCOREBOARD.gold_star_delta_sum[year]
        return render_template('gold.jinja2',
                               scoreboard_name=scoreboard_name,
                               year=year,
                               gold_star_delta=gold_star_delta_sum,
                               )
    abort(404)


@app.route('/about')
def about():
    SCOREBOARD.prepare_scores(THISYEAR)
    scoreboard_name = SCOREBOARD.scoreboard_name[THISYEAR]
    return render_template('about.jinja2',
                           scoreboard_name=scoreboard_name,
                           )


@app.errorhandler(404)
def page_not_found(error):
    SCOREBOARD.prepare_scores(THISYEAR)
    scoreboard_name = SCOREBOARD.scoreboard_name[THISYEAR]
    return render_template('not_found.jinja2',
                           scoreboard_name=scoreboard_name,
                           ), 404


if __name__ == '__main__':
    server_address = ('0.0.0.0', 80)
    print(f"Starting server at {server_address[0]}:{server_address[1]}")
    http_server = WSGIServer(server_address, app)
    try:
        http_server.serve_forever()
    except KeyboardInterrupt:
        print("Goodbye...")
