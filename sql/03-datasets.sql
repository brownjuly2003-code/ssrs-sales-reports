CREATE OR ALTER PROCEDURE dbo.Regions
AS
SELECT RegionId, RegionName FROM dbo.Region ORDER BY RegionName;
GO

CREATE OR ALTER PROCEDURE dbo.Stores @RegionIds nvarchar(max)
AS
SELECT StoreId, StoreName FROM dbo.Store
WHERE @RegionIds IS NULL OR RegionId IN (SELECT TRY_CAST(value AS int) FROM STRING_SPLIT(@RegionIds, ','))
ORDER BY StoreName;
GO

CREATE OR ALTER PROCEDURE dbo.Categories
AS
SELECT CategoryId, CategoryName FROM dbo.Category ORDER BY CategoryName;
GO

CREATE OR ALTER PROCEDURE dbo.StockAtDate
    @EndDate date, @StoreIds nvarchar(max), @CategoryIds nvarchar(max)
AS
SELECT s.StoreId, s.ProductId, s.OnHand FROM dbo.Stock AS s
JOIN dbo.Product AS p ON p.ProductId = s.ProductId
WHERE s.Day = @EndDate
  AND (@StoreIds IS NULL OR s.StoreId IN (SELECT TRY_CAST(value AS int) FROM STRING_SPLIT(@StoreIds, ',')))
  AND (@CategoryIds IS NULL OR p.CategoryId IN (SELECT TRY_CAST(value AS int) FROM STRING_SPLIT(@CategoryIds, ',')));
GO

CREATE OR ALTER PROCEDURE dbo.SnapshotDates
AS
SELECT DISTINCT Day FROM dbo.Stock ORDER BY Day;
GO

CREATE OR ALTER PROCEDURE dbo.ReportRows
    @StartDate date, @EndDate date, @RegionIds nvarchar(max),
    @StoreIds nvarchar(max), @CategoryIds nvarchar(max)
AS
WITH period_sales AS (
    SELECT StoreId, ProductId, SUM(Units) AS Units, SUM(RevenueCents) AS RevenueCents
    FROM dbo.Sales WHERE Day BETWEEN @StartDate AND @EndDate GROUP BY StoreId, ProductId
),
closing AS (
    SELECT StoreId, ProductId, OnHand FROM dbo.Stock WHERE Day = @EndDate
)
SELECT r.RegionId, r.RegionName, st.StoreId, st.StoreName,
       c.CategoryId, c.CategoryName, p.ProductId, p.ProductName,
       COALESCE(s.Units, 0) AS Units, COALESCE(s.RevenueCents, 0) AS RevenueCents,
       COALESCE(b.OnHand, 0) AS OnHand
FROM period_sales AS s
FULL OUTER JOIN closing AS b ON s.StoreId = b.StoreId AND s.ProductId = b.ProductId
JOIN dbo.Store AS st ON st.StoreId = COALESCE(s.StoreId, b.StoreId)
JOIN dbo.Region AS r ON r.RegionId = st.RegionId
JOIN dbo.Product AS p ON p.ProductId = COALESCE(s.ProductId, b.ProductId)
JOIN dbo.Category AS c ON c.CategoryId = p.CategoryId
WHERE @StartDate <= @EndDate
  AND EXISTS (SELECT 1 FROM closing)
  AND (@RegionIds IS NULL OR r.RegionId IN (SELECT TRY_CAST(value AS int) FROM STRING_SPLIT(@RegionIds, ',')))
  AND (@StoreIds IS NULL OR st.StoreId IN (SELECT TRY_CAST(value AS int) FROM STRING_SPLIT(@StoreIds, ',')))
  AND (@CategoryIds IS NULL OR c.CategoryId IN (SELECT TRY_CAST(value AS int) FROM STRING_SPLIT(@CategoryIds, ',')))
ORDER BY r.RegionId, st.StoreId, c.CategoryId, p.ProductId;
GO
