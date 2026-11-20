# The EDGAR ingester (docs/architecture/data-plane.md, section 3), built from the repository root
# (the shared Directory.*.props and ServiceDefaults; .dockerignore lets in only those). Pinned like
# the other images (ADR-0005): both stages by digest, packages from the lock files (--locked-mode).
# No compiler at run time: Wolverine's handler code is generated into the project beforehand
# (`codegen write`, TypeLoadMode.Static) and the runtime image is ASP.NET's, without the SDK.
FROM mcr.microsoft.com/dotnet/sdk:10.0.404-alpine3.24@sha256:0294f285d062afacc8b5f6d7febb0cd4ee879320e5585fa15f2f4ebf1feb9ba7 AS build
WORKDIR /src
COPY global.json nuget.config Directory.Build.props Directory.Packages.props ./
COPY src/dotnet/AetherSpark.ServiceDefaults/AetherSpark.ServiceDefaults.csproj src/dotnet/AetherSpark.ServiceDefaults/packages.lock.json src/dotnet/AetherSpark.ServiceDefaults/
COPY src/dotnet/AetherSpark.Ingester/AetherSpark.Ingester.csproj src/dotnet/AetherSpark.Ingester/packages.lock.json src/dotnet/AetherSpark.Ingester/
RUN dotnet restore src/dotnet/AetherSpark.Ingester --locked-mode
COPY src/dotnet/AetherSpark.ServiceDefaults/ src/dotnet/AetherSpark.ServiceDefaults/
COPY src/dotnet/AetherSpark.Ingester/ src/dotnet/AetherSpark.Ingester/
RUN dotnet publish src/dotnet/AetherSpark.Ingester --no-restore -c Release -o /app

FROM mcr.microsoft.com/dotnet/aspnet:10.0.15-alpine3.24@sha256:21c3f9798416cba4c0576a947c286231cb71ea2884896cb968253f5c9668f930
# No diagnostics socket in /tmp: the ingester has no listener of any kind (D10)
ENV DOTNET_EnableDiagnostics=0
COPY --from=build /app /app
WORKDIR /app
ENTRYPOINT ["dotnet", "/app/AetherSpark.Ingester.dll"]
