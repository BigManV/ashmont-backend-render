from demo_seed_data import build_demo_data, build_kpis


_data = build_demo_data()

leads = _data["leads"]
calls = _data["calls"]
appointments = _data["appointments"]
alerts = _data["alerts"]
outreach_steps = _data["outreach_steps"]


def kpis():
    return build_kpis(_data)
