"""v9 experiment LEAD_AIM: pattern-based lead. Each mech's recent track (TRACK_S of samples) back-tests a few
motion models at a typical bullet flight time; predictions use the model that has been most accurate for
that mech, or, until it has a few scores, the one most accurate over all mechs (house bots share code).

Models (horizontal only; the vertical part stays perception.predict's):
  straight  p + v t
  circular  along an arc at the turn rate seen on the track (v5's model)
  mean      p + v_mean t, v_mean = displacement over the last MEAN_S: a zig-zag strafe averages out
  stand     p (a mech that keeps reversing goes nowhere on average)
"""
import math

from perception import MOTION_MIN_SPEED, OMEGA_MAX, _wrap

MODELS = ("straight", "circular", "mean", "stand")
TRACK_S = 3.0        # keep this much history per mech
MEAN_S = 1.0         # mean velocity over this window
EVAL_H = 0.5         # back-test horizon (about a 120 m gun shot plus latency)
EVAL_TOL = 0.06      # the sample EVAL_H ago may be off by this much
ERR_ALPHA = 0.2      # smoothing of a mech's model errors
POOL_ALPHA = 0.05    # smoothing of the pooled errors
MIN_SCORES = 5       # a mech's own errors count after this many back-tests
TRACK_GAP_S = 1.0    # a track with a longer gap starts over
TIE_M = 0.1          # models within this of the best count as tied: the simpler (earlier in MODELS) wins


def _arc(x, y, vx, vy, w, dt):
    if abs(w) > 1e-3:
        s, c = math.sin(w * dt), math.cos(w * dt)
        return x + (vx * s - vy * (1 - c)) / w, y + (vy * s + vx * (1 - c)) / w
    return x + vx * dt, y + vy * dt


class LeadMixin:
    """Uses self.tracks {id: [(t, x, y, vx, vy)]}, self.lead_err {id: [err per model, n]},
    self.lead_pool [err per model, n] and self.lead_state {id: (model, w, mvx, mvy)}."""

    @staticmethod
    def _track_state(track, i):
        """(turn rate, mean vx, mean vy) of the track as of sample i."""
        t, x, y, vx, vy = track[i]
        w = 0.0
        if i > 0:
            t0, _, _, ux, uy = track[i - 1]
            dt = t - t0
            if 0 < dt <= 0.25 and math.hypot(vx, vy) >= MOTION_MIN_SPEED and math.hypot(ux, uy) >= MOTION_MIN_SPEED:
                w = max(-OMEGA_MAX, min(OMEGA_MAX, _wrap(math.atan2(vy, vx) - math.atan2(uy, ux)) / dt))
        j = i
        while j > 0 and t - track[j - 1][0] <= MEAN_S:
            j -= 1
        if j < i and t - track[j][0] > 0.2:
            span = t - track[j][0]
            mvx, mvy = (x - track[j][1]) / span, (y - track[j][2]) / span
        else:
            mvx, mvy = vx, vy
        return w, mvx, mvy

    @staticmethod
    def _model_xy(model, x, y, vx, vy, w, mvx, mvy, dt):
        if model == "straight":
            return x + vx * dt, y + vy * dt
        if model == "circular":
            return _arc(x, y, vx, vy, w, dt)
        if model == "mean":
            return x + mvx * dt, y + mvy * dt
        return x, y

    def note_tracks(self, obs):
        """Add this observation to each visible mech's track, back-test the models, pick each mech's model."""
        t = obs["t"]
        pool = self.lead_pool
        state = {}
        for m in obs["mechs"]:
            if m.get("kind") != "mech":
                continue
            mid = m["id"]
            x, y = m["pos"][0], m["pos"][1]
            vx, vy = m["vel"][0], m["vel"][1]
            track = self.tracks.get(mid)
            if track is None or t - track[-1][0] > TRACK_GAP_S or t < track[-1][0]:
                track = self.tracks[mid] = []
            if track and track[-1][0] == t:
                track.pop()  # the same observation again
            track.append((t, x, y, vx, vy))
            while t - track[0][0] > TRACK_S:
                track.pop(0)
            # back-test: the sample EVAL_H ago predicting now
            i = len(track) - 1
            while i > 0 and t - track[i][0] < EVAL_H - EVAL_TOL:
                i -= 1
            h = t - track[i][0]
            if abs(h - EVAL_H) <= EVAL_TOL:
                _, ox, oy, ovx, ovy = track[i]
                w, mvx, mvy = self._track_state(track, i)
                errs = []
                for model in MODELS:
                    px, py = self._model_xy(model, ox, oy, ovx, ovy, w, mvx, mvy, h)
                    errs.append(math.hypot(px - x, py - y))
                own = self.lead_err.get(mid)
                if own is None:
                    own = self.lead_err[mid] = [list(errs), 0]
                else:
                    own[0] = [a + ERR_ALPHA * (e - a) for a, e in zip(own[0], errs)]
                own[1] += 1
                if pool[1] == 0:
                    pool[0] = list(errs)
                else:
                    pool[0] = [a + POOL_ALPHA * (e - a) for a, e in zip(pool[0], errs)]
                pool[1] += 1
            own = self.lead_err.get(mid)
            scores = own[0] if own is not None and own[1] >= MIN_SCORES else (pool[0] if pool[1] else None)
            if scores is None:
                model = "circular"
            else:
                lo = min(scores)
                model = next(md for md, e in zip(MODELS, scores) if e <= lo + TIE_M)
            w, mvx, mvy = self._track_state(track, len(track) - 1)
            state[mid] = (model, w, mvx, mvy)
        self.lead_state = state
        for mid in [k for k, tr in self.tracks.items() if t - tr[-1][0] > TRACK_S]:
            del self.tracks[mid]

    def lead_xy(self, m, dt):
        """Horizontal (x, y) of mech m dt seconds from now under its chosen model, or None (no track)."""
        st = self.lead_state.get(m.get("id"))
        if st is None:
            return None
        model, w, mvx, mvy = st
        (px, py), (vx, vy) = m["pos"][:2], m["vel"][:2]
        return self._model_xy(model, px, py, vx, vy, w, mvx, mvy, dt)
