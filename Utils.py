import subprocess
import execjs
from functools import partial

subprocess.Popen = partial(subprocess.Popen, encoding='utf-8', errors='ignore')

def MatchArgs(ver):
    # sg.057.a3c65188d8471228.js
    ver = ver.split('/')[1].split('.')[1]  # 057

    storage = {
        "069": "7sgj4pa0sqsoim5y",
        "067": "c6bpsqhbn472fhwv",
        "092": "1wq8kvqdettgkrza",
        "057": "wyxvlz3kws917hja",
        "066": "zzdvqdfo2mf6wcw4",
        "093": "flrtwnzgvow0yuzy",
        "090": "j8lnj3mjhmqqdbgo",
        "084": "iq9kl163cesrzx0s",
        "050": "cqne8g2fkn7ac1h4",
        "081": "o7noutjmesvbv49e",
        "055": "vmspvoy27hdddcpz",
        "064": "lf1f9dy1xshsnmit",
        "065": "faswhrfio61w28ac",
        "063": "yce9xmbgxr2ah316",
        "075": "plw55e61fvzxe9p0",
        "062": "7wx7qh7l2nnez2dh",
        "086": "qviw3rouflfuw7ie",
        "056": "mlav6knj0lwclyd8",
        "096": "uae0hredtrdnyhy8",
        "052": "ww40n6ms40bx2ofk",
        "072": "z4lo6d9s668o158o",
        "085": "tderj8wf9jaee3cj",
        "070": "qreuitb4zj0l3h3g",
        "087": "cudfq1evgykbbg3r",
        "053": "w6b88b9x6lshrhwh",
        "089": "6hbhh5qas3gui0q5",
        "095": "wq54kc2w76xs8ssz",
        "060": "i3zdq6wl9g3zyx2n",
        "058": "x4aw9ez83xo1lw37",
        "077": "pxvhmbvh66hquzji",
        "088": "6e456nq9cmw2pq6z",
        "083": "ncje263t9hx7lbai",
        "099": "jmmc2kju53r6cc3p",
        "094": "0in5b6vjbl9n5pwj",
        "078": "900lrv0ic7os26qk",
        "068": "rf2ulz1kkgzlmd9f",
        "097": "ac2cp6si9ol40i0y",
        "079": "20nl0nwy6ll25ffm",
        "073": "0gtcm27ory2p720r",
        "098": "0xfn0ub9wnbw7fef",
        "074": "4socnsuwjjwm2dax",
        "076": "35bs1qmlanqlwv40",
        "071": "crivt5vi70ld0haa",
        "051": "s0d9nk73sfh5ko4i",
        "080": "y52cli0h49qrd3qv",
        "082": "ovix9k9svhrqta0s"
    }
    return storage.get(ver, None)


def pwdEncrypt(plain_text):
    js_code = '''
    function pwdEncrypt(pwd) {
        const SM2Utils = require('./sm2');
        return SM2Utils.encs("043b2759c70dab4718520cad55ac41eea6f8922c1309afb788f7578b3e585b167811023effefc2b9193cd93ae9c9a2a864e5fffbf7517c679f40cbf4c4630aa28c", pwd, 1);
    }
    '''
    ctx = execjs.compile(js_code)
    return ctx.call('pwdEncrypt', plain_text)
