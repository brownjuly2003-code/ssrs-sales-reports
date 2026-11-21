IF OBJECT_ID('dbo.Region', 'U') IS NULL
    CREATE TABLE dbo.Region (
        RegionId int NOT NULL,
        RegionName nvarchar(80) NOT NULL,
        PRIMARY KEY (RegionId)
    );

IF OBJECT_ID('dbo.Store', 'U') IS NULL
    CREATE TABLE dbo.Store (
        StoreId int NOT NULL,
        RegionId int NOT NULL,
        StoreName nvarchar(80) NOT NULL,
        PRIMARY KEY (StoreId),
        FOREIGN KEY (RegionId) REFERENCES dbo.Region(RegionId)
    );

IF OBJECT_ID('dbo.Category', 'U') IS NULL
    CREATE TABLE dbo.Category (
        CategoryId int NOT NULL,
        CategoryName nvarchar(80) NOT NULL,
        PRIMARY KEY (CategoryId)
    );

IF OBJECT_ID('dbo.Product', 'U') IS NULL
    CREATE TABLE dbo.Product (
        ProductId int NOT NULL,
        CategoryId int NOT NULL,
        ProductName nvarchar(80) NOT NULL,
        PRIMARY KEY (ProductId),
        FOREIGN KEY (CategoryId) REFERENCES dbo.Category(CategoryId)
    );

IF OBJECT_ID('dbo.Calendar', 'U') IS NULL
    CREATE TABLE dbo.Calendar (
        Day date NOT NULL,
        PRIMARY KEY (Day)
    );

IF OBJECT_ID('dbo.Sales', 'U') IS NULL
    CREATE TABLE dbo.Sales (
        Day date NOT NULL,
        StoreId int NOT NULL,
        ProductId int NOT NULL,
        Units int NOT NULL,
        RevenueCents bigint NOT NULL,
        PRIMARY KEY (Day, StoreId, ProductId),
        FOREIGN KEY (Day) REFERENCES dbo.Calendar(Day),
        FOREIGN KEY (StoreId) REFERENCES dbo.Store(StoreId),
        FOREIGN KEY (ProductId) REFERENCES dbo.Product(ProductId),
        CHECK (Units > 0 AND RevenueCents >= 0)
    );

IF OBJECT_ID('dbo.Stock', 'U') IS NULL
    CREATE TABLE dbo.Stock (
        Day date NOT NULL,
        StoreId int NOT NULL,
        ProductId int NOT NULL,
        OnHand int NOT NULL,
        PRIMARY KEY (Day, StoreId, ProductId),
        FOREIGN KEY (Day) REFERENCES dbo.Calendar(Day),
        FOREIGN KEY (StoreId) REFERENCES dbo.Store(StoreId),
        FOREIGN KEY (ProductId) REFERENCES dbo.Product(ProductId),
        CHECK (OnHand >= 0)
    );

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id = OBJECT_ID('dbo.Sales') AND name = 'IX_Sales_StoreDay')
    CREATE INDEX IX_Sales_StoreDay ON dbo.Sales(StoreId, Day) INCLUDE (ProductId, Units, RevenueCents);
