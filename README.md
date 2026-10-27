# W90 Monte Carlo simulation tool

## Run locally

From the project directory:

```powershell
python -m pip install -r requirements.txt
python -m streamlit run w90_monte_carlo_v8_simapp.py
```

## Deploy a trial app with Streamlit Community Cloud

1. Create a GitHub repository for the app. Add `w90_monte_carlo_v8_simapp.py`,
   `requirements.txt`, and this README.
2. Do not upload project workbooks, weather files, API keys, or other confidential
   information to a public repository. Users can upload their operations workbook
   and ERA5 ZIP files directly in the running app.
3. In Streamlit Community Cloud, create an app from the repository and set the
   main file to `w90_monte_carlo_v8_simapp.py`.
4. Deploy the app and share its URL with the intended testers.

The app has no built-in sign-in. Treat a deployed URL as accessible to anyone
who can reach it unless access controls are explicitly enabled for the deployment.
Uploaded workbooks and weather files are processed by the server running the app;
only use data that is approved for that hosting environment.

For ERA5 API downloads, each user can enter their own API key in the app. The key
is passed directly to the CDS API client and is not written to a shared `.cdsapirc`
file. Do not configure a personal key as a shared Streamlit secret. If an
organization-approved shared key is configured as `ERA5_API_KEY`, all users of
the app will use that credential.
